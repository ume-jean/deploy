
import os,sys
import re
import shutil
import inspect
import subprocess
import logging
import posixpath
import socket
import platform
import pprint

try:
    import simplejson as json
except ImportError:
    import json
    
import datetime
import distutils.dir_util as dir_util

from gtcfg.pkg import BasePkg
from gtcfg.cfg import PkgCfg
import gtcfg.cfg


class PkgEnvError(BaseException):
    pass

class PkgInitError(BaseException):
    pass

class PkgVersionError(BaseException):
    pass

class PkgBuildError(BaseException):
    pass

class PkgDeployError(BaseException):
    pass

class PkgPublishError(BaseException):
    pass

loglevel = logging.INFO
if os.environ.get('_DEBUG_',"False") == "True":   
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
LOG = logging.getLogger(__name__)

class Tag(object):
    _config_fields = []
    def __init__(self, **kw):
        self.id = kw.get("id", None)
        self.commit = kw.get("commit", None)
        self.name = kw.get("name", None)
        self.notes = kw.get("notes", None)
        self.path = kw.get("path", None)
        self.branch = kw.get("branch", None)
    
    def __str__(self):
        data = "{}(".format(self.__class__.__name__)
        for i in inspect.getmembers(self):
            if not i[0].startswith('_'):
                if not inspect.ismethod(i[1]):
                    data += "{}={}, ".format(i[0],i[1])
        return "{})".format(data)
    
    def dump(self):
        data = self.__dict__.copy()
        if self._config_fields:
            _skip = set(data.keys()) - set(self._config_fields)
            for attr in _skip:
                data.pop(attr)
        
        return data
    

class User(object):
    _config_fields = []
    def __init__(self):
        self.login = os.environ.get("USERNAME",os.environ.get("USER"))
        self.home = os.environ.get("USERPROFILE",os.environ.get("HOME")).replace('\\','/')
        self.ssh_key = posixpath.join(self.home,".ssh","id_rsa").replace("\\","/")
        self.hostname = socket.gethostname()
        self.ip = socket.gethostbyname(self.hostname)
    
    def __str__(self):
        data = "{}(".format(self.__class__.__name__)
        for i in inspect.getmembers(self):
            if not i[0].startswith('_'):
                if not inspect.ismethod(i[1]):
                    data += "{}={}, ".format(i[0],i[1])
        return "{})".format(data)
    
    def dump(self):
        data = self.__dict__.copy()
        if self._config_fields:
            _skip = set(data.keys()) - set(self._config_fields)
            for attr in _skip:
                data.pop(attr)
        
        return data    


class Pkg(BasePkg):
    '''
    Class to manage deployment packages
    name=<package_name>
    '''

    
    def __init__(self, **kw):
        super(Pkg, self).__init__(**kw)
        self._refresh = False
        self._builds = []
        self._versions = []
        self._build_tags = []
        self._version_tags = []

    def _get_next_tag(self, **kw):
        tag = Tag()
        if not kw.get('release', False):
            tag.name="rc{}".format(str(int(self.build_tag.name.split("rc")[-1])+1)).zfill(3)
        else:
            major,minor,bug = self.version_tag.name.split(".")
            major = int(major)
            minor = int(minor)
            bug = int(bug)
            if kw['release'] == 'major':
                major += 1
                minor = 0
                bug = 0
            elif kw['release'] == 'minor':
                minor += 1
                bug = 0
            elif kw['release'] == 'bug':
                bug += 1
                
            tag.name="{}.{}.{}".format(major,minor,bug)
        self._get_tag_path(tag, **kw)
        
        return tag
    
    def _get_tag_path(self, tag, **kw):    
        tag.path = os.path.join(self.deploy_root,tag.name).replace('\\','/')
        if not kw.get('release', False):
            tag.path = os.path.join(self.build_root,tag.name).replace('\\','/')

    def _get_tag_commit(self, tag, **kw):
        """
        read it off filesystem
        """
        
        path = os.path.join(self.deploy_root, tag.name, self._buildlog)
        if kw.get('builds',False):
            path = os.path.join(self.build_root, tag.name, self._buildlog)
        try:
            data={}
            with open(path) as bfile:
                data = json.load(bfile)
                tag.commit = data['tag']['commit']
        except:
            pass
   
    def _get_tags(self,**kw):
        '''
        '''
        results = []
        try:
            if not kw.get('release', False):
                tags = self.builds
            else:
                tags = self.versions
                
            for tag_ref in tags:
                tag = Tag(**{'name': tag_ref})
                self._get_tag_commit(tag,**kw)
                self._get_tag_path(tag)
                results.append(tag)
            
            if not kw.get('release',False):
                self._build_tags = results
            else:
                self._version_tags = results
                
            return results
        
        except Exception as e:
            LOG.exception("Unable to get tags for [{}] >> {}\n".format(self.name,e))
            raise e
    
    def _sync_to_version(self, version, **kw):
        self._merge_version(version, **kw)
    
    def _merge_version(self, version, **kw):
        version_path = os.path.join(self.deploy_root, version)
        dir_util.copy_tree(version_path, self.root_path)
        if os.path.exists(os.path.join(self.root_path, self._buildlog)):
            os.remove(os.path.join(self.root_path, self._buildlog))
        

    
    @property
    def versions(self):
        '''
        need caching
        '''
        if not self._versions or self._refresh:
            self._versions = []
            v_regex = re.compile(r'^(\d+?.\d+?.\d+?)')
            if os.path.exists(self.deploy_root):
                path_list = os.listdir(self.deploy_root)
                for item in os.listdir(self.deploy_root):
                    path = posixpath.join(self.deploy_root, item)
                    if os.path.isdir(path):
                        match = self._valid_version.match(item)
                        if match:
                            self._versions.append(item)
            
            self._versions.sort(key=lambda v: [int(n) for n in v.split('.')])
            
        return self._versions
    
    @property
    def builds(self):
        if not self._builds or self._refresh:
            self._builds = []
            if os.path.exists(self.build_root):
                path_list = os.listdir(self.build_root)
                for item in os.listdir(self.build_root):
                    path = posixpath.join(self.build_root, item)
                    if os.path.isdir(path):
                        match = self._valid_build.match(item)
                        if match:
                            self._builds.append(item)
        
            self._builds.sort(key=lambda x: int(x.split('rc')[-1]))
        
        return self._builds
    
    @property
    def build_tag(self):
        if len(self.build_tags) > 0:    
            return self.build_tags[-1]
        else:
            return Tag(name="rc0")
        
    @property
    def version_tag(self):
        if len(self.version_tags) > 0:
            return self.version_tags[-1]
        else:
            return Tag(name="0.0.0")
    
    @property
    def version_tags(self):
        if not self._version_tags or self._refresh:
            self._version_tags = self._get_tags(release=True)
        return self._version_tags
    
    @property
    def build_tags(self):
        if not self._build_tags or self._refresh:
            self._build_tags = self._get_tags()
        return self._build_tags
    
    def create_build_log(self, **kw):
        user = kw.get('user') or User()
        tag = kw.get('tag', {})
        log = {'date': datetime.datetime.now().strftime("%y/%m/%d-%H:%M"),
               'user': user.dump(),
               'tag': tag.dump(),
               'pkg': self.dump() }
        if kw.get('dump',False):
            with open(posixpath.join(tag.path, self._buildlog),'w') as bfile:
                json.dump(log, bfile, indent=4)
                
        return log
    
    def create_release_notes(self, build_log, pkg,**kw):
        """
        """
        notes = "\n===== [{}][{}] Release Notes =====\n".format(pkg.name, pkg.version)
        notes += "Notes: \n{}\n\n".format(kw.get('notes','auto-publish'))
        #path = os.path.join(self.deploy_root)
        path = os.path.join(self.deploy_root, pkg.version)
        notes_path = posixpath.join(path, "[{}] {}".format(pkg.version, self._release_notes))
        with open(notes_path,'w') as bfile:
                notes += pprint.pformat(build_log)
                bfile.write(notes)
        return notes_path    
    
    def build(self, **kw):
        return self.build_release(**kw)
    
    def deploy(self, release,**kw):
        build_tag = self._get_next_tag()
        self.build(tag=build_tag, **kw)
        return self.deploy_release(release, build_tag, **kw)
    
    def publish(self, version, **kw):
        info =  self.publish_release(version, **kw)
        #gtcfg.cfg.publish_configs(repo=True, **kw)
        return info
    
    def build_release(self, **kw):
        '''
        stub w/o unit testing
        '''
        tag = kw.get('tag')
        if not tag:
            tag = self._get_next_tag()
        if os.path.exists(tag.path) and kw.get('force',None):
            shutil.rmtree(tag.path)
        shutil.copytree(self.dev_root, tag.path,
                        ignore=lambda directory, contents: ['.git'] if directory == self.dev_root else [])
        return self.create_build_log(tag=tag, dump=True)
   
    def deploy_release(self, release, build_tag, **kw):
        '''
        stub copy package from build root to deploy root
        '''
        if self.root in ('app') or self.name == "cfg-db":
            return
        #whatever branch you're on just put it on network
        #leave it to user to update repo
        deploy_ignore = ['.git','.pyc','.pyo','.gitignore']
        tag = kw.get('tag',None)
        if not tag:
            tag = self._get_next_tag(release=release)
        self.version = tag.name
        
        if os.path.exists(tag.path):
            if kw.get('force',False):
                shutil.rmtree(tag.path)
            else:
                raise PkgDeployError("[{}] exists!! Use force option to overwrite".format(tag.path))
            
        shutil.copytree(build_tag.path, tag.path,
                        ignore=lambda directory, contents: deploy_ignore if directory == build_tag.path else [])
        
        return self.create_build_log(tag=tag, dump=True)
    
    def publish_release(self, version, **kw):
        '''
        update targeted config 
        '''
        
        if version not in self.versions:
            raise PkgPublishError("{} [{}] does not exist!".format(self.name, version))
        
        project = kw.get('project','default')
        if self.root == 'cfg':
            project = 'default'
            
        #get target config
        #init a temp copy of configs
        _cfg_list = gtcfg.cfg.get_configs('pkg')
        _CfgChain = gtcfg.cfg.CfgChain(cfg_type='pkg', cfg_list=_cfg_list)
        _PkgCfg = _CfgChain.find_one(value=project)
        
        #creste new config if needed
        if not _PkgCfg:            
            _cfg_list.sort(key=lambda cfg: int(cfg.id))
            next_cfg_id = int(_cfg_list[-1].id)+1
            _PkgCfg = gtcfg.cfg.init_cfg({"type":'pkg','id':next_cfg_id,'code':project.lower()})
        try:
            #load build log and create release notes
            build_log_path = os.path.join(self.deploy_root, version, self._buildlog)
            build_log = {}
            
            if os.path.exists(build_log_path):    
                with open(build_log_path) as bfile:
                    build_log = json.load(bfile)
                    #init publish package from build_log
                    pub_pkg = BasePkg(**build_log['pkg'])
            if not pub_pkg:
                #init publish package from self
                pub_pkg = BasePkg(**self.dump())
                #set version to target version
                pub_pkg.version = version
            #create release notes for the publish package
            notes_path = self.create_release_notes(build_log, pub_pkg,**kw)
            
            #update target config with publish package entry        
            _PkgCfg.upsert(pub_pkg.dump())
            #write config to cfg-db
            gtcfg.cfg.put_configs([_PkgCfg],repo=True)
            
            #if this publish package is of type 'cfg' sync to version in GT_CONFIG_ROOT
            if self.root =='cfg':
                self._sync_to_version(version, merge="True")
                note_file = os.path.basename(notes_path)
                if os.path.exists(os.path.join(self.root_path,note_file)):
                    shutil.move(os.path.join(self.root_path,note_file),os.path.join(self.root_path, self.name, note_file))
                #write release notes
                #shutil.copyfile(notes_path, r_notes)

            return pub_pkg.dump()
            
        except Exception as err:
            raise PkgPublishError(err)

 

def unittest():
    '''
    TODO: make real unittest :)
    '''
    
    #import gtcfg.resolve
    #import pprint
    #env = gtcfg.resolve.environment()
    #_pkg = gtcfg.resolve.packages(packages=["deployer"])[0]
    _pkg = Pkg(name="deployer")
    print _pkg.path
    #result = _pkg.deploy_release()
    
    #print _pkg.path
    #pprint.pprint(_pkg.dump())
    
    

    #
    #
    #os.environ["GT_DEV_ROOT"] = "C:/Users/jean.mistrot/dev"
    #import gtcfg
    #gtcfg.resolve.environment()
    #PkgRepo(name="gtdevpkg")
    #
    
    
if __name__ == '__main__':
    unittest()
    