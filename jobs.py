
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



from gtcfg.repo import RepoPkg


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
LOG = logging.getLogger("deployer")


#helper methods
def build_job(**params):
    '''
    simple builder DOES NOT currently build per platform
    '''
    
    
    success = False
    os.environ["GT_DEV_ROOT"] = os.environ['WORKSPACE']
    
    _Pkg = RepoPkg(**json.loads(params['package']))
    _Pkg.sync_remotes()
    _Pkg.fetch_changes()
    _Pkg.current_branch = params['branch']
    _Pkg.checkout_commit(params['commit'])
    
    if params.get('verbose',False):
        pp(_Pkg.dump())
    
    
    build_config = _Pkg.get_build_config()
    if build_config:
        if params.get('verbose',False):
            pp(build_config)
        success = eval(build_config['build_cmd'])
    else:
        success = True
    
    if success:
        #tag and update server
        _Pkg.create_version(**params)
        #create build log
        _Pkg.create_build_log(**params)
        
        #deploy build
        if os.path.exists(_Pkg.path) and kw.get('force',None):
            shutil.rmtree(_Pkg.path)
        shutil.copytree(_Pkg.dev_root, _Pkg.path,
                            ignore=lambda directory, contents: ['.git'] if directory == _Pkg.dev_root else [])
        
    
    return success    

    
def deploy_job(**kw):
    _Pkg = repopkg.RepoPkg(**json.loads(kw['package']))
    if kw.get('verbose',False):
        pp(kw)
        pp(_Pkg.dump())


def publish_job(**kw):
    _Pkg = repopkg.RepoPkg(**json.loads(kw['package']))
    
    if kw.get('verbose',False):
        pp(kw)
        pp(_Pkg.dump())

registered_jobs = {
        'build': build_job,
         'deploy': deploy_job,
         'publish': publish_job
         } 