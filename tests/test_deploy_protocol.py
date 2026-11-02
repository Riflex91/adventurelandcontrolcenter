import io,os,re,subprocess,tarfile,tempfile,unittest,shutil
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings
from alcc.workspaces import Workspaces
from alcc.remote import RaspberryPi

class ProtocolTest(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.t=Path(t.name)
        self.settings=Settings(self.t/'data');self.settings.values['workspace_dir']=str(self.t/'work')
        self.ws=Workspaces(self.settings);self.pi=RaspberryPi(self.settings,self.ws)
        b=io.BytesIO()
        with tarfile.open(fileobj=b,mode='w:gz') as z:
            content=b'const a=1;\n';i=tarfile.TarInfo('CODE/newbot.js');i.size=len(content);z.addfile(i,io.BytesIO(content))
        self.name=self.ws.create_from_tar(b.getvalue(),'/home/riflex/CODE/newbot-coordinator/releases/original')['name']
        f=self.ws.read_candidate(self.name,'CODE/newbot.js')
        self.ws.save_candidate(self.name,'CODE/newbot.js','const a=2;\n',f['sha256'])
        self.ws.test_receipt(self.name,'node',0,'# tests 1\n# pass 1')
    def test_stale_base_prevents_deployment(self):
        with patch.object(self.pi,'status',return_value={'release':'/home/riflex/CODE/newbot-coordinator/releases/changed'}):
            with self.assertRaisesRegex(ValueError,'Release, Änderungen'):
                self.pi.deploy(self.name,self.ws.plan(self.name)['plan_sha256'],'DEPLOY '+self.name)
    def test_shell_script_generated_is_valid_bash_and_audited(self):
        logs=[]
        bash = shutil.which('bash')
        if os.name == 'nt':
            git_bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git' / 'bin' / 'bash.exe'
            if git_bash.is_file(): bash = str(git_bash)
            elif not bash: self.skipTest('Bash interpreter not available on Windows CI')
        if not bash: self.skipTest('Bash interpreter not available')
        def fake_call(body,timeout=30,max_output=0):
            logs.append(body)
            result=subprocess.run([bash,'-n'],input=(self.pi._vars()+body).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            m=re.search(r'(?m)^id=(alcc-[A-Za-z0-9-]+)',body)
            if m:
                target='/home/riflex/CODE/newbot-coordinator/releases/'+m.group(1)
                return {'exit_code':0,'stdout':'RELEASE='+target+'\nPREVIOUS=/home/riflex/CODE/newbot-coordinator/releases/original\n','stderr':''}
            return {'exit_code':0,'stdout':'','stderr':''}
        with patch.object(self.pi,'status',return_value={'release':'/home/riflex/CODE/newbot-coordinator/releases/original'}), \
             patch.object(self.pi,'call',side_effect=fake_call), \
             patch('alcc.remote.run_command',return_value={'exit_code':0,'stdout':'','stderr':''}):
            r=self.pi.deploy(self.name,self.ws.plan(self.name)['plan_sha256'],'DEPLOY '+self.name)
        self.assertIn('/releases/alcc-',r['new_release'])
        self.assertTrue(any('sha256sum -c' in script for script in logs))
        self.assertTrue(any('DEPLOY_ROLLED_BACK' in script for script in logs))
        self.assertTrue(any('STALE_RELEASE' in script for script in logs))
        self.assertTrue((self.settings.data/'audit.jsonl').exists())
        self.assertFalse((self.ws.location(self.name)/'candidate-upload.tar.gz').exists())
    def test_rollback_requires_current_release_match(self):
        self.ws.add_deploy_receipt(self.name,{'new_release':'/home/riflex/CODE/newbot-coordinator/releases/deployed','previous_release':'/home/riflex/CODE/newbot-coordinator/releases/original'})
        with patch.object(self.pi,'status',return_value={'release':'/home/riflex/CODE/newbot-coordinator/releases/not-ours'}):
            with self.assertRaisesRegex(ValueError,'Aktiver Release'):
                self.pi.rollback(self.name,'ROLLBACK '+self.name)

if __name__=='__main__':unittest.main()
