
def submit_build(self, name, **kw):
    '''
    commit changes
    push changes
    submit build server
    '''
    _Pkg = RepoPkg(name=name)
    _Pkg.repo.index.add(['.'])
    _Pkg.repo.index.commit(kw,get('message','auto-commit'))
    self.build_server.submit_job(name='build-deploy',
                                 release=['release'],
                                 package = _Pkg.name,
                                 branch = _Pkg.branch.name,
                                 commit = _Pkg.commit.name)

def submit_publish(self,**kw):
    self.build_server.submit_job(name='publish',
                                 project=kw['project'],
                                 version=kw['version'],
                                 package = self.name)    


if __name__ == '__main__':
    import os, sys
    import argparse
    import logging
    from pprint import pprint as pp
    
    if not os.environ.get("GT_CONFIG_VER"):    
        import gtcfg.resolve
        gtcfg.resolve.environment()
    
    desc = 'Deploy/Publish git repositories\n'
    #desc += 'Deploy/Publish git repositories\n'
    parser = argparse.ArgumentParser(description=desc)
    group = parser.add_mutually_exclusive_group()
    parser.add_argument('-g','--gitrepo', help='The package name you want to deploy.')
    group.add_argument('-r','--release', help='The release type (major,minor,bug).', choices=['major','minor','bug'])
    group.add_argument('-b','--branch', help='Repo branch name to tag use only in conjunction with bug RELEASE.')
    group.add_argument('-v','--version', help='The package semantic version number (n.n.n) to publish.')
    parser.add_argument('-p','--project', help='The project to which to publish the package version.')
    parser.add_argument('-n','--notes', help='Notes when deploying or publishing..')
    parser.add_argument('-dbg','--debug', action='store_true',help="Run in debug mode.")
    parser.add_argument('-ui','--ui', action='store_true',help="Open the depoyer ui.")
    
    args = parser.parse_args()
    action=None
    action_arg=None
    project = args.project
    branch = None
    
    loglevel = logging.INFO
    os.environ["_DEBUG_"]= "False"
    if args.debug:
        os.environ["_DEBUG_"]= "True"
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
    
    if args.ui:
        import app
        app.run(args)
        sys.exit(0)
    else:
        
        if not project:
            project = os.environ.get("PROJECT", "default")
        if args.version:
            action="publish"
            action_arg = args.version
        if args.release:
            action="deploy"
            action_arg = args.release
            #if args.release == 'bug' and not args.branch:
            #    print "Release [] requires that a valid branch be specified using [-b --branch] flag..".format(args.release )
            #    parser.print_help()
            #    sys.exit(0)
            #else:
            #    branch = args.branch
            #
            
        if args.gitrepo and action and action_arg:
            
            import repopkg
            from buildserver import BuildServer
            _BuildServer = BuildServer()
            
            args.gitrepo = os.path.basename(args.gitrepo)
            _pkg_list = gtcfg.resolve.packages("default", packages=[args.gitrepo],overrides=[])
            if not _pkg_list:
                _Pkg = repopkg.RepoPkg(name=args.gitrepo)
            else:
                _Pkg = repopkg.RepoPkg(**_pkg_list[0].dump())
            
            #set project
            _Pkg.project = args.project
            
            
            if action == 'deploy':            
                _Pkg.stage_changes()
                _Pkg.commit_changes(notes="auto-commit")
                _Pkg.push_changes()
                #send to server to build and deploy
                result = _BuildServer.submit_job(name=action, release=args.release, package=json.dumps(_Pkg.dump()), notes=args.notes)
                
            if action == 'publish':
                result = _BuildServer.submit_job(name=action, package=json.dumps(_Pkg.dump()), notes=args.notes)
                
            #if action == 'deploy':
            #    version =result['pkg']['version']
            #else:
            #    version = action_arg
            #
            #print "SUCCESS>> ====== {}ed -> {} [{}] ======\n".format(action, args.gitrepo, version)
            #if args.debug:
            #    pp(result)
            sys.exit(0)
        else:
            parser.print_help()
            sys.exit(0)