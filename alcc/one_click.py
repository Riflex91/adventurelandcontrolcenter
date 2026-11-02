"""User-initiated one-click offline verification + guarded Raspberry Pi deployment.

The UI button itself is the explicit operator action. All original workspace,
receipt, live-release, checksum, SSH and rollback gates are retained.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from .core import audit_event


def _test_modes(api, workspace):
    entries = [item['path'] for item in api.ws.candidate_file_list(workspace)]
    def is_node(rel):
        p = PurePosixPath(rel)
        return (p.suffix in ('.js','.cjs','.mjs') and (
            p.name.startswith('test-') or p.name.startswith('test_') or
            '.test.' in p.name or '.spec.' in p.name or
            (len(p.parts) > 1 and p.parts[0] in ('test','tests','__tests__'))))
    node = any(is_node(rel) for rel in entries)
    py = any(PurePosixPath(rel).name.startswith('test') and rel.endswith('.py') for rel in entries)
    npm = False
    if 'package.json' in entries:
        # package.json itself is validated by the separate syntax step.
        p=api.ws.location(workspace)/'candidate'/'package.json'
        try:
            doc=json.loads(p.read_text('utf8'))
            npm=bool(isinstance(doc,dict) and isinstance(doc.get('scripts'),dict)
                     and isinstance(doc['scripts'].get('test'),str)
                     and doc['scripts']['test'].strip())
        except (OSError,ValueError):
            pass
    modes=[]
    if py: modes.append('python')
    if npm: modes.append('npm')
    if node: modes.append('node')
    return modes


def run_one_click(api, workspace):
    """Run only on explicit one-click user action; never from startup or sync."""
    if not isinstance(workspace,str) or not workspace.strip():
        raise ValueError('Bitte den Kandidaten-Arbeitsbereich auswählen')
    if not api._one_click_lock.acquire(blocking=False):
        return {'ok':False,'deployed':False,'stage':'busy',
                'error':'Ein Ein-Klick-Deployment läuft bereits'}
    stage='preflight'; tests=[]; preview=None; remote_status=None
    try:
        audit_event(api.logs,'one_click_start',workspace=workspace)
        plan=api.ws.plan(workspace)
        if not plan['changed']:
            raise ValueError('Keine lokalen Änderungen: nichts zu deployen')
        remote_status=api.pi.status()
        if remote_status.get('release')!=plan['base_release']:
            raise ValueError('Pi-Release hat sich geändert. Bitte Arbeitsbereich erst abgleichen; keine Übertragung.')
        if remote_status.get('service')!='active':
            raise ValueError('Pi-Bot-Dienst ist nicht aktiv; kein automatischer Neustart/Deploy')
        # Stop markers are never deleted or automatically interpreted as approval.
        # They constrain autonomous coordinator work; manual Pi deployment retains
        # the original RaspberryPi.preview/deploy eligibility checks.
        stops=api.coord.stops()
        stage='syntax'
        check=api.test_candidate(workspace,'syntax','TEST '+workspace)
        tests.append({'mode':'syntax','passed':check['passed'],'checked':check['checked'],
                      'output':check['output'][-5000:]})
        if not check['passed']:
            raise ValueError('Syntaxprüfung fehlgeschlagen')
        modes=_test_modes(api,workspace)
        if not modes:
            raise ValueError('Keine Funktionstests im Kandidaten vorhanden; Deployment gesperrt')
        for mode in modes:
            stage='tests:'+mode
            report=api.test_candidate(workspace,mode,'TEST '+workspace)
            tests.append({'mode':mode,'passed':report['passed'],
                          'eligible_for_deploy':report['eligible_for_deploy'],
                          'checked':report['checked'],'output':report['output'][-9000:]})
            if not report['passed'] or not report['eligible_for_deploy']:
                raise ValueError('Funktionstests fehlgeschlagen: '+mode)
        stage='preview'
        preview=api.pi.preview(workspace)
        if not preview.get('deploy_eligible'):
            raise ValueError('Deployment-Vorschau nicht freigegeben: Basisrelease, Testbeleg oder Dateien prüfen')
        stage='deploy'
        # The single deliberate button press is the authorization. The original
        # deployment method still independently verifies plan_sha, live release,
        # hashes, receipts and performs rollback on failed service health checks.
        receipt=api.pi.deploy(workspace,preview['plan_sha256'],'DEPLOY '+workspace)
        audit_event(api.logs,'one_click_success',workspace=workspace,
                    new_release=receipt['new_release'])
        return {'ok':True,'deployed':True,'stage':'complete','workspace':workspace,
                'tests':tests,'stop_markers':stops,'preview':{
                    'deploy_eligible':True,'changed':preview.get('changed',[]),
                    'plan_sha256':preview['plan_sha256']},
                'new_release':receipt['new_release'], 'previous_release':receipt['previous_release']}
    except Exception as exc:
        audit_event(api.logs,'one_click_blocked',workspace=workspace,
                    stage=stage,error_type=type(exc).__name__)
        return {'ok':False,'deployed':False,'stage':stage,'error':str(exc),
                'workspace':workspace,'tests':tests,
                'preview':{'deploy_eligible':preview.get('deploy_eligible',False)} if preview else None,
                'remote_status':remote_status}
    finally:
        api._one_click_lock.release()
