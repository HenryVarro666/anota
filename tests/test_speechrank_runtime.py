"""Runtime contracts use synthetic bytes/models; never touch frozen artifacts."""
import json
from types import SimpleNamespace

import pytest

from speechrank import models
from speechrank import runtime as rt


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    calls = []
    fake_models = {}
    hashes = {}
    for arm in ('legacy', 'confidence'):
        payload = arm.encode()
        model = SimpleNamespace(speechrank_feature_arm_=arm,
            speechrank_feature_version_=rt.FEATURE_VERSION,
            speechrank_feature_names_=list(rt.FEATURE_NAMES[arm]),
            speechrank_candidate_={'candidate_id': 'fake'},
            n_features_in_=len(rt.FEATURE_NAMES[arm]))
        fake_models[arm] = model
        card = {'schema_version': 'speechrank-model-card-v1', 'feature_arm': arm,
            'feature_version': rt.FEATURE_VERSION, 'feature_names': list(rt.FEATURE_NAMES[arm]),
            'sklearn_version': '1.9.1', 'sha256': rt._hash(payload),
            'target': 'gold_edit_errors / audio_duration_s', 'sample_weight': 'audio_duration_s',
            'candidate': model.speechrank_candidate_}
        for name, data in [(f'model-{arm}.joblib', payload),
                           (f'model-{arm}.json', json.dumps(card).encode())]:
            (tmp_path / name).write_bytes(data)
            hashes[name] = rt._hash(data)
    monkeypatch.setattr(rt, '_APPROVED', hashes)
    monkeypatch.setenv('ANOTA_SPEECHRANK_BUNDLE', str(tmp_path))
    def load(stream):
        arm = stream.read().decode()
        calls.append(('load', arm))
        return fake_models[arm]
    monkeypatch.setattr(rt, '_dependencies', lambda: (SimpleNamespace(load=load), SimpleNamespace(__version__='1.9.1')))
    def predict(model, rows, arm):
        calls.append(('predict', arm, len(rows)))
        return [0.2 if arm == 'legacy' else 0.3 for _ in rows]
    monkeypatch.setattr(models, 'predict_density', predict)
    rt._MODELS.clear()
    rt._PREDICTIONS.clear()
    yield tmp_path, calls, fake_models
    rt._MODELS.clear()
    rt._PREDICTIONS.clear()


def row(**kwargs):
    return {'audio_duration_s': 2.0, 'asr_text': 'hello world', 'status': 'succeeded',
            'language': 'en', 'model': dict(rt.SUPPORTED_DOMAIN['model']),
            'config': dict(rt.SUPPORTED_DOMAIN['config']), **kwargs}


def test_route_batch_cache_and_no_gold(bundle):
    _, calls, _ = bundle
    source = [row(), row(words=[{'probability': .8}]), row(segments=[{'avg_logprob': -.2}])]
    result = rt.score_rows(source)
    assert [r['arm'] for r in result] == ['legacy', 'confidence', 'confidence']
    assert all(r['status'] == 'ok' and r['bundle_fingerprint'] for r in result)
    assert [c for c in calls if c[0] == 'predict'] == [('predict', 'legacy', 1), ('predict', 'confidence', 2)]
    source[0]['golden_answers'] = {'do not read': 99}
    assert rt.score_rows(source) == result
    assert len([c for c in calls if c[0] == 'predict']) == 2
    assert not any('gold' in key for record in result for key in record)


@pytest.mark.parametrize('fields', [
    {'language_probability': 1}, {'words': [{'probability': None}]},
    {'words': [{'probability': True}]}, {'words': [{'probability': '0.8'}]},
    {'words': [{'probability': float('nan')}]}, {'words': [{'probability': -1}]},
    {'segments': [{'avg_logprob': 0.1, 'no_speech_prob': .2}]},
])
def test_missing_bad_confidence_uses_legacy(bundle, fields):
    assert rt.score_rows([row(**fields)])[0]['arm'] == 'legacy'


@pytest.mark.parametrize('fields,reason', [
    ({'language': 'zh'}, 'unsupported_language'),
    ({'model': {}}, 'asr_provenance_mismatch'),
    ({'config': {}}, 'asr_provenance_mismatch'),
    ({'config': None}, 'asr_provenance_missing'),
    ({'words': [None]}, 'invalid_asr_features'),
    ({'audio_duration_s': 0}, 'invalid_asr_features'),
])
def test_invalid_or_unsupported_input(bundle, fields, reason):
    result = rt.score_rows([row(**fields)])[0]
    assert result['status'] == 'unavailable' and result['reason'] == reason


def test_missing_and_tampering_rejected_before_load_even_cached(bundle):
    path, calls, _ = bundle
    assert rt.score_rows([row()])[0]['status'] == 'ok'
    calls.clear()
    (path / 'model-legacy.joblib').write_bytes(b'tampered')
    assert rt.score_rows([row()])[0]['reason'] == 'artifact_hash_mismatch'
    assert not calls
    (path / 'model-legacy.joblib').unlink()
    result = rt.score_rows([row()])[0]
    assert result['reason'] == 'bundle_missing'
    assert str(path) not in json.dumps(result)


def test_manifest_cannot_authorize_replacement_pickle(bundle):
    path, calls, _ = bundle
    manifest = rt.bundle_manifest()
    manifest['files']['model-legacy.joblib'] = rt._hash(b'hostile')
    (path / 'manifest.json').write_text(json.dumps(manifest))
    assert rt.score_rows([row()])[0]['reason'] == 'bundle_manifest_mismatch'
    assert not calls


def test_dependency_unavailable_safe(bundle, monkeypatch):
    def fail():
        raise rt.RuntimeUnavailable('optional_dependencies_unavailable')
    monkeypatch.setattr(rt, '_dependencies', fail)
    assert rt.score_rows([row()])[0]['reason'] == 'optional_dependencies_unavailable'


def test_optional_import_failure(monkeypatch):
    def fail(_):
        raise ImportError('private local path should not escape')
    monkeypatch.setattr(rt.importlib, 'import_module', fail)
    with pytest.raises(rt.RuntimeUnavailable, match='^optional_dependencies_unavailable$'):
        rt._dependencies()


def test_schema_drift_before_load(bundle, monkeypatch):
    path, calls, _ = bundle
    card_path = path / 'model-legacy.json'
    card = json.loads(card_path.read_text())
    card['feature_names'].reverse()
    data = json.dumps(card).encode()
    card_path.write_bytes(data)
    monkeypatch.setitem(rt._APPROVED, card_path.name, rt._hash(data))
    assert rt.score_rows([row()])[0]['reason'] == 'model_schema_mismatch'
    assert not calls


def test_model_schema_drift(bundle):
    _, _, fake_models = bundle
    fake_models['legacy'].speechrank_feature_names_.reverse()
    assert rt.score_rows([row()])[0]['reason'] == 'model_schema_mismatch'


@pytest.mark.parametrize('prediction', [float('nan'), float('inf'), -1])
def test_invalid_prediction(bundle, monkeypatch, prediction):
    monkeypatch.setattr(models, 'predict_density', lambda *args: [prediction])
    assert rt.score_rows([row()])[0]['reason'] == 'invalid_prediction'
    assert not rt._PREDICTIONS


def test_content_changes_recompute(bundle):
    _, calls, _ = bundle
    rt.score_rows([row(), row()])
    assert ('predict', 'legacy', 1) in calls
    calls.clear()
    rt.score_rows([row(asr_text='changed')])
    assert calls == [('predict', 'legacy', 1)]


def test_new_bundle_fingerprint_never_uses_stale_model(bundle, monkeypatch):
    _, calls, _ = bundle
    first = rt.score_rows([row()])[0]
    calls.clear()
    monkeypatch.setitem(rt._PROVENANCE, 'run_id', 'synthetic-next-reviewed-release')
    second = rt.score_rows([row()])[0]
    assert second['bundle_fingerprint'] != first['bundle_fingerprint']
    assert calls == [('load', 'legacy'), ('predict', 'legacy', 1)]


def test_version_checked_before_load(bundle, monkeypatch):
    _, calls, _ = bundle
    monkeypatch.setattr(rt, '_dependencies', lambda: (None, SimpleNamespace(__version__='wrong')))
    assert rt.score_rows([row()])[0]['reason'] == 'sklearn_version_mismatch'
    assert not calls
