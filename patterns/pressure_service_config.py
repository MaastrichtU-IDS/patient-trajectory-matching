"""Resolve a local pressure-service configuration and freeze its review inputs."""
from copy import deepcopy
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from . import mapped_pressure_session as mapped

ei, cr = mapped.ei, mapped.cr
PROFILE = 'mapped-pressure-service-config-1.0'
MAX_CONFIG_BYTES = 64 * 1024
FILES = ('patterns/pressure_service_config.py', 'schemas/pressure-service-config.schema.json')
SCHEMA = ei.ROOT / FILES[1]


def read_json(path, limit=cr.MAX_BYTES):
    with Path(path).open('rb') as handle: raw = handle.read(limit+1)
    ei.require(len(raw) <= limit, 'PRESSURE_CONFIG_INPUT_LIMIT')
    return json.loads(raw), mapped.source.inputs.source.digest(raw)


def controls_for(request):
    treatment, item = mapped.pressure.p.windows.validate(request)
    q = request['query']; b, f = q['baseline'], q['followup']
    ei.require(b['operator'] == 'lt' and b['min_before_start_us'] == 1 and
               f['anchor'] == 'start' and f['min_after_anchor_us'] == 0 and not f['within_interval'],
               'UNSUPPORTED_CONFIGURED_PRESSURE_WINDOWS')
    ei.require(b['unit_lexical'] == f['unit_lexical'] == 'mmHg', 'CONFIGURED_PRESSURE_REQUIRES_MMHG')
    ei.require(b['max_before_start_us'] % 60_000_000 == 0 and f['max_after_anchor_us'] % 60_000_000 == 0,
               'CONFIGURED_PRESSURE_REQUIRES_WHOLE_MINUTES')
    controls = mapped.pressure.controls({'threshold':b['value_lexical'],
        'baseline_minutes':b['max_before_start_us']//60_000_000,
        'followup_minutes':f['max_after_anchor_us']//60_000_000})
    return controls, treatment, item


def load(path):
    path = Path(path).resolve(); value, digest = read_json(path, MAX_CONFIG_BYTES)
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(value)), 'INVALID_PRESSURE_SERVICE_CONFIG')
    paths = {name:(path.parent/value[name]).resolve() for name in ('source_dir','pressure_request','source_review','mapping_dir')}
    # Establish table presence/ambiguity, but leave source admission to the real audit.
    mapped.source.inputs.paths_for(paths['source_dir'])
    mapped.pressure.p.windows.inputs.source_paths(paths['source_dir'])
    request, request_sha = read_json(paths['pressure_request'])
    declaration, declaration_sha = read_json(paths['source_review'])
    defaults, treatment, item = controls_for(request)
    schema = mapped.pressure.r.SCHEMA
    ei.require(not list(Draft202012Validator(json.loads(schema.read_text())).iter_errors(declaration)), 'INVALID_CONFIGURED_SOURCE_REVIEW')
    _, mapping_stamps = mapped.load_pack(paths['mapping_dir'])
    context = {'profile':PROFILE, 'id':value['id'], 'label':value['label'], 'configuration_sha256':digest,
               'request_sha256':request_sha, 'source_review_sha256':declaration_sha, 'mapping_files':mapping_stamps,
               'source_scope':'supplied_records_not_publisher_authenticated',
               'artifacts':{f:ei.digest((ei.ROOT/f).read_text()) for f in FILES}}
    return {'path':path, 'paths':paths, 'context':context, 'context_id':cr.digest(context),
            'request':deepcopy(request), 'declaration':deepcopy(declaration), 'defaults':defaults,
            'treatment_item':treatment, 'measurement_item':item}


def check_current(snapshot):
    context = snapshot['context']; paths = snapshot['paths']
    ei.require(read_json(snapshot['path'],MAX_CONFIG_BYTES)[1] == context['configuration_sha256'] and
               read_json(paths['pressure_request'])[1] == context['request_sha256'] and
               read_json(paths['source_review'])[1] == context['source_review_sha256'] and
               mapped.load_pack(paths['mapping_dir'])[1] == context['mapping_files'],
               'PRESSURE_CONFIGURATION_CHANGED_RESTART_SERVER')
