import os,sys
import errno
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
    import ujson as json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        import json
    

from git.repo import Repo
import gtcfg.resolve 
from buildserver import BuildClient

#module imports
from pkg import Pkg, User, Tag

class RepoServerInitError(BaseException):
    pass

class RepoServerError(BaseException):
    pass

class RepoPkgEnvError(BaseException):
    pass

class RepoPkgInitError(BaseException):
    pass

class RepoPkgVersionError(BaseException):
    pass

class RepoPkgError(BaseException):
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

class RepoTag(Tag):
    def __init__(self, **kw):
        super(RepoTag, self).__init__(**kw)


class RepoServerClient(object):
    """
    wrapper class to communicate with GitLab server
    server connectino is initialized via config found in ./resrc
    """
    _required_env = ("GT_REPO_SERVER","GT_REPO_TOKEN","GT_REPO_USER","GT_REPO_ROOT")
    _resrc = os.path.join(os.path.dirname(__file__),"resrc")
    _config = os.path.join(os.path.dirname(__file__),"resrc","gitlab.config")
    _dev_group = "gtdev"
    _server_stub = None
    def __init__(self,**kw):
        for evar in RepoPkg._required_env:
            try:
                os.environ[evar]
            except Exception as err:
                raise RepoServerError("Required environment variable [{}] not found!!".format(evar))
        self.name = os.environ['GT_REPO_SERVER']
        self.token = os.environ['GT_REPO_TOKEN']
        self.user = os.environ['GT_REPO_USER']
        self.root = os.environ['GT_REPO_ROOT']
        self._repos = []
        self._users = []
        self._dev_group = None
        self._local_user =  User()
        self._refresh = False
    
    def __str__(self):
        data = "{}(".format(self.__class__.__name__)
        for i in inspect.getmembers(self):
            if not i[0].startswith('_'):
                if not inspect.ismethod(i[1]):
                    data += "{}={}, ".format(i[0],i[1])
        return "{})".format(data)
    
    def _create_repo(self, name):
        try:
            self.server_stub.projects.create({'name': name, 'namespace_id': self.dev_group.id })
        except Exception as err:
            raise RepoServerError(err)
            
    def _delete_repo(self, name):
        try:            
            self.server_stub.projects.delete(self.find_repo(name).id)
        except Exception as err:
            raise(RepoServerError(err))

  
    @property
    def dev_group(self):
        if not self._dev_group or self._refresh:
            gl_groups = self.server_stub.groups.search("gtdev")
            if gl_groups:
                self._dev_group = gl_groups[0]
        return self._dev_group
    
    @property
    def local_user(self):
        return self._local_user
    
    @local_user.setter
    def local_user(self, user):
        if isinstance(user, User) and user.login in self.users:
            self._local_user = user
            
    @property
    def repos(self):
        if not self._repos or self._refresh:
            self._repos = self._get_repos()
        return self._repos
    
    @property
    def users(self):
        if not self._users or self._refresh:
            self._users = self._get_dev_users()
        return self._users
    
    @property
    def server_stub(self):
        if not self._server_stub or self._refresh:        
            try:
                import gitlab
                self._server_stub = gitlab.Gitlab("http://{}".format(self.name),self.token)
            except Exception as err:
                raise RepoServerInitError("Unable to connect to GitLab server {}".format(err))
        return self._server_stub
    
    def refresh(self):
        '''
        re-cache data from server
        '''
        self._refresh=True
        for i in inspect.getmembers(self):
            if not i[0].startswith('_'):
                if not inspect.ismethod(i[1]):
                    eval("self.{}".format(i[0]))
        self._refresh = False
    
    def find_repo(self, name):
        result = None
        repos = [r for r in self.repos if r.name == name]
        if repos:
            result = repos[0]
        return result
    
    def has_repo(self, name):
        return bool(self.find_repo(name))
    
    def create_repo(self, name):
        self._create_repo(name)
    
    def delete_repo(self, name):
        self._delete_repo(name)
    
    def clone_repo(self, name, path):
        Repo.clone_from("{}@{}:{}/{}.git".format(self.user,self.name,self.root,name),path)
        
    def _get_dev_users(self):
        return [self.server_stub.users.get(m.id) for m in self.dev_group.members.list()]
    
    def _get_repos(self):
        return self.server_stub.projects.list()
    

class RepoPkg(Pkg):
    """
    Class to manage deployment package Git repos
    name=<package_name>
    """

    _repo_server = None
    _config_fields = ['name',
                      'root',
                      'version',
                      'platform',
                      'type',
                      'project']
    
    
    def __init__(self, **kw):
        super(RepoPkg, self).__init__(**kw)
        self._version_tags = []
        self._build_tags=[]
        self._deployed_versions = []
        self._deployed_builds = []
        
        self._tags = None
        self._repo = None
        self._repo_server_client = kw.get('repo_server', None)
        self._build_server_client = kw.get('build_server', None)
        self._force = kw.get('force',True)
        self._project = kw.get('project','dft')
        self._pub_version =  self._get_published_version()
        if kw.get('current_branch',None):
            self.current_branch = kw['current_branch']
        if kw.get('current_commit',None):
            self.current_commit = kw['current_commit']
        
    def _init_repo(self, **kw):
        try:
            init_folder = False
            if not os.path.exists(self.dev_root):
                if self._force:
                    if not self.repo_server.has_repo(self.name):
                        self.repo_server.create_repo(self.name)
                        init_folder=True
                    self.repo_server.clone_repo(self.name, self.dev_root)
                    if self.type == 'module' and init_folder:
                        local_repo_path = os.path.join(self.dev_root, self.name)
                        os.makedirs(local_repo_path)
                        shutil.copy(os.path.join(self.repo_server._resrc,".gitignore"),self.dev_root)
                    
                else:
                    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), self.dev_root)
            self._repo = Repo(self.dev_root)
            if init_folder:
                self.stage_changes()
                self.commit_changes("first_commit")
                self.push_changes()
                
        except Exception as err:
            raise RepoPkgInitError(err)

    def _get_published_version(self):
        '''
            query config for published version of pkg
        '''
        result = self.version
        pkg_list = gtcfg.resolve.packages(project=self._project,packages=[self.name,],overrides=[])
        if len(pkg_list) > 0:
            result = pkg_list[0].version
        return result
    
    def _set_upstream(self):
        try:
            os.chdir(self.repo.working_tree_dir)
            proc = subprocess.Popen("git push -u origin {}".format(self.current_branch.name),
                                  shell=True,
                                  stderr=subprocess.PIPE,
                                  stdout=subprocess.PIPE)
            out, err = proc.communicate()
            exit_status = proc.returncode
            if err:
                result=True

            #LOG.info(out)
            #LOG.error(err)
            if exit_status:
                raise Exception("out:{}\nerr:{}".format(out,err))
        except Exception as e:
            raise e

    def _version_tags_on_commit(self):
        return [t.name for t in self.version_tags if self.current_commit == t.commit]
    
    def _build_tags_on_commit(self):
        return [t.name for t in self.build_tags if self.current_commit == t.commit]
    
    def _get_next_version(self,release='minor'):
        '''
        take the aggreagate of deployed and tags
        '''
        regex = re.compile(r"(\d+?\.\d+?\.\d+?)$")
        versions = [self.version]
        if bool(self.latest_version_tag):
            versions.append(self.latest_version_tag.name)
        if bool(self.deployed_versions):
            versions.append(self.deployed_versions[-1])
            
        versions.sort(key=lambda t: [int(u) for u in t.split('.')], reverse=True)
        major,minor,bug = versions[0].split(".")
        major = int(major)
        minor = int(minor)
        bug = int(bug)
        if release == 'major':
            major += 1
            minor = 0
            bug = 0
        elif release == 'minor':
            minor += 1
            bug = 0
        elif release == 'bug':
            bug += 1
        result = "{}.{}.{}".format(major,minor,bug)
        return result
        
    def _get_next_build(self):
        '''
        take the aggreagate of deployed and tags
        '''
        
        regex = re.compile(r"(rc\d+)$")
        builds = ["rc0"]
        
        if self.latest_build_tag:
            builds.append(self.latest_version_tag.name)
        if self.deployed_builds:
            builds.append(self.deployed_builds[-1])
        builds.sort(key=lambda t: [int(u) for u in t.split('rc')[-1]], reverse=True)    
            
        return "rc{}".format(int(builds[0].split("rc")[-1])+1)
         


############### Public Methods #######################
    def dump(self):
        '''
        dump object as json
        '''
        data = self.__dict__.copy()
        data['version'] = data.pop("_version")
        if not data['platform']:
            data.pop('platform')
        if self._config_fields:
            _skip = set(data.keys()) - set(self._config_fields)
            for attr in _skip:
                data.pop(attr)
        data['current_commit'] = "{}".format(self.current_commit)
        data['current_branch'] = "{}".format(self.current_branch)
        data['pub_version'] = self.pub_version
        
        return data
    
        #return json.dumps(data,indent=4)
    
    def build_release(self, **kw):
        '''
        - load build config
        - build all platforms that match current
        - copy artifacts to network
        '''
        
        tag = kw.get('tag')
        if not tag:
            tag = self._get_next_tag()
        if os.path.exists(tag.path) and kw.get('force',None):
            shutil.rmtree(tag.path)
        shutil.copytree(self.dev_root, tag.path,
                        ignore=lambda directory, contents: ['.git'] if directory == self.dev_root else [])
        return self.create_build_log(tag=tag, dump=True)
    
    def build(self, force=True):
        '''
        - if commit already has build on disk and status success or running
            - reject
        - else
            - tag commit
            - send package to build server client
        '''
        params = {}
        if self.has_build_tag() and not force:
        #check on disk
            if self.build_tag.name in self.deployed_builds:
            #check for log on disk
                build_log = self.get_build_log(self.build_tag.name)
                if build_log['status'] != "success":
                    raise(RepoPkgError("[{} - {}] already exists..".format(self.name, self.build_tag.name)))
                    
        #check on build server 
            else:
                build_job_info = self.build_server.get_job_info('build')
                build_job = self.build_server_client.get_running_job_info('build')
                if build_job and build_job['params']['build_tag'] == self.build_tag.name:
                    raise(RepoPkgError("[{} - {}] build id({})in prgress...".format(self.name, self.build_tag.name, build_job['id'])))
                    
                    
        build_info = self.build_server_client.submit_job('build', **self.dump())
            
        return build_info
    
    def deploy(self, release,**kw):

        try:
            self.commit_changes(**kw)
            build_log = self.build(**kw)
            tag = self.tag_changes(release=release,**kw)
            tag = self._get_tag(tag.name,release=release, **kw)
            
        except Exception as err:
            if not kw.get('force', False):
                msg = "{} >> Use force option to ignore..".format(err)
                raise RepoPkgError(msg)
    
    def publish(self, release,**kw):

        try:
            self.commit_changes(**kw)
            build_log = self.build(**kw)
            tag = self.tag_changes(release=release,**kw)
            tag = self._get_tag(tag.name,release=release, **kw)
            
        except Exception as err:
            if not kw.get('force', False):
                msg = "{} >> Use force option to ignore..".format(err)
                raise RepoPkgError(msg)
            
        return self.deploy_release(release, Tag(**build_log['tag']), tag=tag,**kw)
    
    def has_version_tag(self):
        return bool(self._version_tags_on_commit())
    
    def has_build_tag(self):
        return bool(self._build_tags_on_commit())
    
    def create_version(self,release="minor"):
        '''
        tag current commit
        
        '''
        if self.current_branch.name == 'master':
            LOG.error(" versioning [{}] branch is prohibited...".format(self.current_branch.name))
            return
        
        if self._has_version_tag():
            LOG.error(" Current commit already has version tags {} ...".format(self._version_tags_on_commit()))
            return
        
        version = self.get_next_version(release_type)
        self.tag_changes(version)
        
        return version
    
    def get_next_version_tag(self, release='minor'):
        '''
        '''
        result = self.version
        if self.latest_version_tag:
            major,minor,bug = self.latest_version_tag.name.split(".")
            major = int(major)
            minor = int(minor)
            bug = int(bug)
            if release == 'major':
                major += 1
                minor = 0
                bug = 0
            elif release == 'minor':
                minor += 1
                bug = 0
            elif release == 'bug':
                bug += 1
            result = "{}.{}.{}".format(major,minor,bug)
        return result
    
    def get_next_build_tag(self,**kw):
        return "rc{}".format(int(self.latest_build_tag.name.split("rc")[-1])+1)
    
    def stage_changes(self):
        return self.repo.git.add(A=True)
        
    def commit_changes(self, notes='auto-commit'):
        return self.repo.index.commit(notes)
    
    def tag_changes(self, name):
        return self.repo.tag(name)
    
    def push_tags(self):
        return self.repo.remote('origin').push("--tags")
    
    def push_changes(self):
        if not bool(self.current_branch.tracking_branch()):
            self._set_upstream()
        return self.repo.remote('origin').push()
    
    def pull_changes(self):
        return self.repo.remote('origin').pull()
    
    def fetch_changes(self):
        for remote in self.repo.remotes:
            remote.update()

################## Properties ##################           
    @property
    def pub_version(self):
        return self._get_published_version()
    
    @property
    def next_version(self):
        return self._get_next_version()
    
    @property
    def next_build(self):
        return self._get_next_build()
    
    @property
    def project(self):
        return self._project
    
    @project.setter
    def project(self, value):
        self._project = 'value'
        self._pub_version = self._get_published_version()
    
    @property
    def has_changes(self):
        return self.repo.is_dirty()
    
    @property
    def repo(self):
        if not self._repo:
            self._init_repo()
        return self._repo
    
    @property
    def repo_server_client(self):
        if not self._repo_server_client:
            self._repo_server_client = RepoServerClient()
        return self._repo_server_client
    
    @property
    def build_server_client(self):
        if not self._build_server_client:
            self._build_server_client = BuildServerClient()
        return self._build_server_client
    
    @property
    def current_branch(self):
        return self.repo.active_branch
    
    @current_branch.setter
    def current_branch(self, value):
        if value not in [b.name for b in self.branches]:
            return
        self.repo.heads[value].checkout() 
        return self.repo.active_branch
    
    @property
    def current_commit(self):
        return self.current_branch.commit

    @current_commit.setter
    def current_commit(self, value):
        '''
        HEAD pointer to specific commit by hash
        '''
        if value not in [c.hexsha for c in self.branches]:
            return
        self.repo.heads[value].checkout() 
        return self.repo.active_branch

    @property
    def branches(self):
        return self.repo.repo.branches

    @property
    def tags(self):
        return self.repo.tags
    
    @property
    def latest_version_tag(self):
        result = None
        if len(self.version_tags) > 0:
            result = self.version_tags[0]
        return result
    
    @property
    def latest_build_tag(self):
        result = None
        if len(self.build_tags) > 0:
            result = self.build_tags[0]
        return result
    
    @property
    def version_tags(self):
        results = []
        regex = re.compile(r"(\d+?\.\d+?\.\d+?)$")
        for tag in self.tags:
            if regex.match(tag.name):
                results.append(tag)
        results.sort(key=lambda t: [int(u) for u in t.name.split('.')], reverse=True)
        return results
    
    @property
    def build_tags(self):
        results = []
        regex = re.compile(r"(rc\d+)$")
        for tag in self.tags:
            if regex.match(tag.name):
                results.append(tag)
        results.sort(key=lambda t: [int(u) for u in t.name.split('rc')[-1]], reverse=True)
        return results
    
    @property
    def deployed_versions(self):
        '''
        need caching
        '''
        if not self._deployed_versions or self._refresh:
            self._deployed_versions = []
            v_regex = re.compile(r'^(\d+?.\d+?.\d+?)')
            if os.path.exists(self.deploy_root):
                path_list = os.listdir(self.deploy_root)
                for item in os.listdir(self.deploy_root):
                    path = posixpath.join(self.deploy_root, item)
                    if os.path.isdir(path):
                        match = self._valid_version.match(item)
                        if match:
                            self._deployed_versions.append(item)
            
            self._deployed_versions.sort(key=lambda v: [int(n) for n in v.split('.')])
            
        return self._deployed_versions
    
    @property
    def deployed_builds(self):
        if not self._deployed_builds or self._refresh:
            self._deployed_builds = []
            if os.path.exists(self.build_root):
                path_list = os.listdir(self.build_root)
                for item in os.listdir(self.build_root):
                    path = posixpath.join(self.build_root, item)
                    if os.path.isdir(path):
                        match = self._valid_build.match(item)
                        if match:
                            self._deployed_builds.append(item)
        
            self._deployed_builds.sort(key=lambda x: int(x.split('rc')[-1]))
        
        return self._deployed_builds


def unittest():
    '''
    TODO: make real unittest :)
    '''
    from pprint import pprint as pp
    print "TESTING"
    
    _RepoServer = RepoServer()
    
    
    #builder
    
    pp(_RepoServer)
    print _RepoServer.repos
    #pp( [u.name for u in _RepoServer.users])
    #print _RepoServer.local_user
    
    #_RepoPkg = RepoPkg(name="gtdevpkg")
    #tags = _RepoPkg.build_tags
    #vtags = _RepoPkg.version_tags
    #for t in tags:
    #    print t
    
    #_pkg = gtcfg.resolve.packages(packages=["deployer"])[0]
    #_pkg = Pkg(name="deployer")
    #print _pkg.path
    #result = _pkg.deploy_release()
    
    #print _pkg.path
    #pprint.pprint(_pkg.dump())
    
    
    
    #init pkg
    #make change
    
    #build(deploy=False)
    #   - changes
    #       - add
    #       - commit
    #       - push_flag = True
    #   - no rc tag
    #       - tag commmit
    #       - push_flag = True
    #   - push_flag?
    #       - push to origin
    #   - build_server_job(build,pkg_name,branch,tag,**kwargs)
    #   - return job id
    
    #deploy(release=minor,tag=<latest rc>)
    #   - get rc-commit
    #   - no version tag on rc-commit
    #       - tag rc-commit
    #       - clean orphan rc tags (all tags on commits that don't also have version tags)
    #       - push tags to origin
    #   - build_server_job(deploy, **kwargs)
    
    #build_deploy()
    #   - build(deploy=True)
    #   - build_server_job(deploy, build_job_id, **kwargs)
    
    #BUILD SERVER
    
    #build (pkg_name, branch, tag, deploy=False)
    #   - instantitate pkg repo
    #   - clone/sync to branch and tag
    #   - build using build.cfg to network
    #       - create child platform build jobs
    
    #deploy (pkg_name, release, build_tag=<latest rc>)
    #   - instantitate pkg repo
    #   - if not build_tag query for latest build tag if required
    #   - read build_log.json from source
    #   - update pkg version
    #   - tag pkg repo with version tag
    #   - clean orphan rc tags (all tags on commits that don't also have version tags)
    #   - deploy pkg
    #   - update build_log.json
    
    
    #builder
    
    #pp(_RepoServer)
    #print _RepoServer.repos
    #pp( [u.name for u in _RepoServer.users])
    #print _RepoServer.local_user
    
    #_RepoPkg = RepoPkg(name="gtdevpkg")
    #tags = _RepoPkg.build_tags
    #vtags = _RepoPkg.version_tags
    #for t in tags:
    #    print t
    
    #_pkg = gtcfg.resolve.packages(packages=["deployer"])[0]
    #_pkg = Pkg(name="deployer")
    #print _pkg.path
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
    
