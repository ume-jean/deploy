

def run(args):
    print "Starting Deployer UI"
    if args.debug:
        from pprint import pprint as pp
        print "{} Args {}".format("="*5,"="*5)
        pp(args.__dict__)