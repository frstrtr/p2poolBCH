import os
import re
import sys
import traceback
import subprocess

def check_output(*popenargs, **kwargs):
    process = subprocess.Popen(stdout=subprocess.PIPE, *popenargs, **kwargs)
    output, unused_err = process.communicate()
    retcode = process.poll()
    if retcode:
        raise ValueError((retcode, output))
    return output

def _get_version():
    try:
        try:
            return check_output(['git', 'describe', '--always', '--dirty'], cwd=os.path.dirname(os.path.abspath(sys.argv[0]))).strip()
        except:
            pass
        try:
            return check_output(['git.cmd', 'describe', '--always', '--dirty'], cwd=os.path.dirname(os.path.abspath(sys.argv[0]))).strip()
        except:
            pass

        root_dir = os.path.abspath(os.path.dirname(sys.argv[0]))
        git_dir = os.path.join(root_dir, '.git')
        if os.path.exists(git_dir):
            head = open(os.path.join(git_dir, 'HEAD')).read().strip()
            prefix = 'ref: '
            if head.startswith(prefix):
                path = head[len(prefix):].split('/')
                return open(os.path.join(git_dir, *path)).read().strip()[:7]
            else:
                return head[:7]

        # Container/release fallback: a VERSION file written at image-build
        # time, or P2POOL_VERSION env var injected by the entrypoint / CI.
        # Without these we'd fall through to the 'unknown <hex>' branch which
        # surfaces as "unknown 7032706f6f6c" (= 'p2pool' in hex) on the
        # dashboard whenever git isn't available in the container.
        for candidate in (os.path.join(root_dir, 'VERSION'),
                          os.path.join(os.path.dirname(__file__), '..', 'VERSION')):
            try:
                with open(os.path.abspath(candidate)) as f:
                    v = f.read().strip()
                    if v:
                        return v
            except Exception:
                pass
        env_v = os.environ.get('P2POOL_VERSION', '').strip()
        if env_v:
            return env_v

        dir_name = os.path.split(root_dir)[1]
        match = re.match('p2pool-([.0-9]+)', dir_name)
        if match:
            return match.groups()[0]

        return 'unknown %s' % (dir_name.encode('hex'),)
    except Exception, e:
        traceback.print_exc()
        return 'unknown %s' % (str(e).encode('hex'),)

__version__ = _get_version()

DEBUG = True
