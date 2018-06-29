
import os,sys
from pprint import pprint as pp
import logging

try:
    import ujson as json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        import json


import jenkins as jenkins
from jenkins import NotFoundException

import repopkg

os.environ["_LOCAL_DEBUG_"]="True"
loglevel = logging.INFO
if os.environ.get("_DEBUG_","False") == "True":
    loglevel = logging.DEBUG

#KomodoIDE Remote Debugging
remote_brk = lambda: sys.stdout.write("remote break")
if os.environ.get("_REMOTE_DEBUG_",'False') == 'True':
    try:
        from dbgp.client import brk
        remote_brk = lambda: brk(host=os.environ.get("REMOTE_DEBUG_HOST","127.0.0.1"),
                                 port=int(os.environ.get("REMOTE_DEBUG_PORT",'9000')))
    except:
        pass

logging.basicConfig(level=loglevel)
LOG = logging.getLogger("buildserver")


class JenkinsPollError(Exception):
    pass


class JenkinsQueueError(Exception):
    pass


class JenkinsBuildError(Exception):
    pass


class JenkinsError(Exception):
    pass



class BuildServerClient(jenkins.Jenkins):
    '''
    '''
    
    def __init__(self):
        super(BuildServer, self).__init__("jenkins-server",
                                         "cuser",
                                         "!cuser123")

    def build_local_job(self, job_info):
        import jobs
        return jobs.registered_jobs[job_info['name']](**job_info['params'])


    def submit_job(self, job_name, **params):
        try:
            if os.environ.get('_LOCAL_DEBUG_',"False") == "True":
                build_number = 1
                params['verbose'] = True
            else:
                info = self.get_job_info(job_name)
                build_number = info['nextBuildNumber']
                self.set_action(job_name)
                
            job_info = {}
            job_info['params'] = params
            job_info['id'] = build_number
            job_info['name'] = job_name
            
            
            if os.environ.get('_LOCAL_DEBUG_',"False") == "True":
                return self.build_local_job(job_info)
            else:
                self.build_job(job_name, parameters=params,
                               token=os.environ['GT_BUILDS_SERVER_TOKEN'])
                return job_info
        except jenkins.JenkinsException as e:
            raise(e)
        

def unittest():
    p = repopkg.RepoPkg(name="gtdevpkg")
    bs = BuildServer()
    bs.submit_job('build',package=json.dumps(p.dump()))
    bs.submit_job('deploy',package=json.dumps(p.dump()),release='minor')
    
    p.version = p.pub_version
    bs.submit_job('publish',package=json.dumps(p.dump()))
    
        
if __name__ == '__main__':
    unittest()