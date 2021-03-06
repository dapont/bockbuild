import re
import glob
import os
import sys
import subprocess
import fileinput
import inspect
import time
import difflib
import shutil
import tarfile
import hashlib
import stat
import functools

# from
# https://svn.blender.org/svnroot/bf-blender/trunk/blender/build_files/scons/tools/bcolors.py


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class config:
    trace = False
    filter = None  # function name/package name filter for trace() and test()
    test = False
    iterative = False  # FIXME: this needs a bit more work
    quiet = None
    never_rebuild = False
    verbose = False


class CommandException (Exception):  # shell command failure

    def __init__(self, message, cwd=None):
        if cwd is None:
            cwd = os.getcwd()
        Exception.__init__(self, '%s: %s (path: %s)' %
                           (get_caller(), cwd, message))
        verbose(message)


# internal/unexpected issue, treat as unrecoverable
class BockbuildException (Exception):

    def __init__(self, message):
        Exception.__init__(self, message)


class Logger:
    last_header = None
    print_color = False
    monkeywrench = False


def log(phase, message):
    # DISABLED until we can properly refactor/redirect logging
    return

# TODO: move these functions to either Profile or their own class


def get_caller(skip=0, get_dump=False):
    # this whole thing fails if we're not in a valid directory
    try:
        cwd = os.getcwd()
    except OSError as e:
        return '~could not get caller (current directory not valid)~'

    stack = inspect.stack(3)
    if len(inspect.stack()) < 3 + skip:
        return 'top'
    output = None
    last_caller = None
    for record in stack[2 + skip:]:
        caller = record[3]
        frame = record[0]

        try:
            if 'self' in frame.f_locals:
                try:
                    output = '%s->%s' % (frame.f_locals['self'].name, caller)
                except Exception as e:
                    pass
                finally:
                    if output is None:
                        output = '%s->%s' % (
                            frame.f_locals['self'].__class__.__name__, caller)
            else:
                last_caller = caller
            if get_dump:
                output = output + "\n" + \
                    "\t".join(dump(frame.f_locals['self'], 'self'))

        except Exception as e:
            pass
        if output is not None:
            return output

    if output is None:
        return last_caller


def assert_exists(path):
    if not os.path.exists(path):
        error('assert_exists failed: ' + os.path.basename(path))


def loginit(message):
    if os.getenv('BUILD_REVISION') is not None:  # MonkeyWrench
        print '@MonkeyWrench: SetSummary:<h3>%s</h3>' % message
        Logger.monkeywrench = True
    elif sys.stdout.isatty():
        Logger.print_color = True
        logprint(message, bcolors.BOLD)


def colorprint(message, color):
    if Logger.print_color:
        print '%s%s%s' % (color, message, bcolors.ENDC)
    else:
        print message


def logprint(message, color, summary=False, header=None, trace=False):
    if isinstance(message, str):
        lines = message.split('\n')
    elif isinstance(message, dict):
        lines = list()
        for k in message.keys():
            lines.append('%s : %s' % (k, message[k]))
    else:  # assume iterable
        lines = message

    if config.quiet == True and trace == False:
        return
    if summary:
        if Logger.monkeywrench:
            for line in lines:
                print '@MonkeyWrench: AddSummary:<p>%s</p>' % line
            return

    if header != Logger.last_header:
        Logger.last_header = header
        print
        if header is not None:
            colorprint('%s:' % header, color)

    for line in lines:
        output = ''
        if header is not None:
            output = '\t'
        output = output + '%s' % line.rstrip('\r\n')
        colorprint(output, color)


def title(message, summary=True):
    logprint('\n** %s **\n' % message, bcolors.HEADER, summary)


def info(message, 	summary=True):
    logprint(message, bcolors.OKGREEN, summary, header='info')


def progress(message):
    logprint(message, bcolors.OKBLUE, header=get_caller())


def verbose(message):
    if not config.verbose and not config.trace:
        return
    logprint(message, bcolors.OKBLUE, header=get_caller())


def warn(message):
    if isinstance(message, str):
        message = '%s %s' % ('(bockbuild warning)', message)
    logprint(message, bcolors.FAIL, header=get_caller())


def error(message, more_output=False):
    config.trace = False
    if isinstance(message, str):
        message = '%s %s' % ('(bockbuild error)', message)
    logprint(message, bcolors.FAIL, header=get_caller(), summary=True)
    if not more_output:
        sys.exit(255)


def trace(message, skip=0):
    if config.trace == False:
        return

    caller = get_caller(skip)

    if config.filter is not None and config.filter not in caller:
        return

    logprint(message, bcolors.FAIL, summary=True, header=caller, trace=True)


def test(func):
    if config.test == False:
        return
    caller = get_caller()

    if config.filter is not None and config.filter not in caller:
        return

    if func() == False:
        error('Test ''%s'' failed.' % func.__name__)


def retry(func, tries=3, delay=5, **kwargs):
    result = None
    exc = None
    cwd = os.getcwd()
    result = None
    for x in range(tries):
        try:
            os.chdir(cwd)
            result = func(**kwargs)
            break
        except CommandException as e:
            if x == tries - 1:
                error(str(e))
            info(str(e))
            info("Retrying ''%s'' in %s secs" % (func.__name__, delay))
            time.sleep(delay)

    return result


def ensure_dir(d, purge=False):
    trace('ensuring:' + d)
    if os.path.exists(d):
        if not purge:
            return

        verbose('Nuking %s' % d)
        unprotect_dir(d, recursive=True)
        delete(d)

    os.makedirs(d)


# quick and dirty assuming they have the same name/paths
def identical_files(first, second):
    hash1 = hashlib.sha1(open(first).read()).hexdigest()
    hash2 = hashlib.sha1(open(second).read()).hexdigest()

    return hash1 == hash2


def md5(path):
    return hashlib.md5(open(path).read()).hexdigest()


def compare_text(new, old):
    difflines = [line for line in difflib.context_diff(old, new, n=0)]
    if len(difflines) > 0:
        changes = [line.rstrip('\r\n') for line in difflines if line.startswith(
            ('+ ', '- ', '! '))]
        return changes
    else:
        return None


def is_changed(new, file, show_diff=True):
    orig = []

    if os.path.exists(file):
        with open(file) as input:
            orig = [line.rstrip('\r\n') for line in input.readlines()]

    else:
        return len(new) > 0

    diff = compare_text(new, orig)

    if diff is not None:
        if show_diff:
            map(lambda x: info(x), diff)
        return True
    else:
        return False


def get_filetype(path):
    # the env variables are to work around a issue with OS X and 'file':
    # https://trac.macports.org/ticket/38771
    return backtick('LC_CTYPE=C LANG=C file -b "%s"' % path)[0]

# http://stackoverflow.com/questions/377017/test-if-executable-exists-in-python


def which(program):
    def is_exe(fpath):
        return os.path.exists(fpath) and os.access(fpath, os.X_OK)

    def ext_candidates(fpath):
        yield fpath
        for ext in os.environ.get("PATHEXT", "").split(os.pathsep):
            yield fpath + ext

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            for candidate in ext_candidates(exe_file):
                if is_exe(candidate):
                    return candidate

    return None


def find_git(self, echo=False):
    git_bin = which('git')
    if not git_bin:
        error('git not found in PATH')

    def git_func(self, args, cwd):
        # assert_git_dir(self)
        (exit, out, err) = run(git_bin, args.split(' '), cwd)
        return out.split('\n')

    self.git = git_func.__get__(self, self.__class__)
    self.git_bin = git_bin


def assert_git_dir(self):
    try:
        self.git('rev-parse HEAD')
    except:
        error('Attempted git action in non-git directory')


def git_get_revision(self, cwd):
    return self.git('rev-parse HEAD', cwd)[0]


def git_get_branch(self, cwd):
    revision = git_get_revision(self, cwd)
    try:
        output = self.git('symbolic-ref -q --short HEAD', cwd)
    except:
        return None  # detached HEAD
    else:
        return output[0]


def git_is_dirty(self, cwd):
    str = self.git('symbolic-ref --short HEAD --dirty', cwd)[0]
    return 'dirty' in str


def git_patch(self, dir, patch):
    self.git('diff > %s' % patch)


def git_shortid(self, cwd):
    branch = git_get_branch(self, cwd)
    short_rev = self.git('describe --abbrev --always --dirty', cwd)[0]
    if branch is None:
        return short_rev
    else:
        return '%s-%s' % (branch, short_rev)


def protect_dir(path, recursive=False):
    if not os.path.isdir(path):
        error('only safe for dirs: %s' % path)

    os.chmod(path, stat.S_IRUSR | stat.S_IXUSR)

    if recursive:
        for root, subdirs, filelist in os.walk(path):
            protect_dir(root, recursive=False)


def unprotect_dir(path, recursive=False):
    if not os.path.isdir(path):
        error('only safe for dirs: %s' % path)

    os.chmod(path, stat.S_IRUSR | stat.S_IXUSR | stat.S_IWUSR |
             stat.S_IXGRP | stat.S_IRGRP | stat.S_IXOTH | stat.S_IROTH)

    if recursive:
        for root, subdirs, filelist in os.walk(path):
            unprotect_dir(root, recursive=False)

# wrap around shutil.rmtree, which is unreliable. Sometimes a few attempts
# do the trick...


def delete(path):
    trace('deleting %s' % path)
    if not os.path.isabs(path):
        raise BockbuildException('Relative paths are not allowed: %s' % path)

    if not os.path.lexists(path):
        raise CommandException('Invalid path to rm: %s' % path)

    if os.getcwd() == path:
        raise BockbuildException(
            'Will not delete current directory: %s' % path)

    # get the dir out of the way so that we don't have to deal with
    # inconsistent state if we fail
    if os.path.isfile(path):
        os.remove(path)
        return

    # directory removal
    if os.path.islink(path):
        os.unlink(path)
        return

    orig_path = path
    unprotect_dir(path, recursive=True)
    path = path + '.deleting'
    if os.path.exists(path):
        delete(path)

    shutil.move(orig_path, path)
    for x in range(1, 5):
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=False)
        except OSError as e:
            pass
        finally:
            if not os.path.exists(path):
                break
        warn('retrying delete of %s' % path)
        # try to sabotage whoever else is writing in the directory...
        protect_dir(path, recursive=True)
        time.sleep(1)
        unprotect_dir(path, recursive=True)

    if os.path.exists(path):
        error('Deleting failed: %s' % orig_path)


def merge_trees(src, dst, delete_src=True):
    if not os.path.isdir(src) or not os.path.isdir(dst):
        raise Exception('"%s" or "%s" are not both directories ' % (src, dst))
    run_shell('rsync -a --ignore-existing %s/* %s' % (src, dst), False)
    if delete_src:
        delete(src)


def iterate_dir(dir, with_links=False, with_dirs=False, summary=False):
    x = 0
    links = 0
    dirs = 0

    for root, subdirs, filelist in os.walk(dir):
        dirs = dirs + 1
        if with_dirs:
            yield root
        for file in filelist:
            path = os.path.join(root, file)
            if os.path.islink(path):
                links = links + 1
                if with_links:
                    yield path
                continue
            x = x + 1
            yield path

    if summary:
        info("%s: %s files, %s dirs, %s symlinks" %
             (os.path.relpath(dir, os.getcwd()), x, dirs, links))


def zip(src, archive):
    x = 0
    # thanks to http://stackoverflow.com/a/17080988

    pwd = os.getcwd()

    try:
        os.chdir(src)
        with tarfile.open(archive, "w:gz") as zip:
            for path in iterate_dir(src, with_links=True, with_dirs=True, summary=False):
                relpath = os.path.relpath(path, src)
                zip.add(relpath, recursive=False)
                x = x + 1
    finally:
        os.chdir(pwd)


def unzip(archive, dst):
    if os.path.exists(dst):
        raise Exception('unzip: Destination should not exist: %s' % dst)

    pwd = os.getcwd()
    relroot = os.path.abspath(os.path.join(dst, os.pardir))

    try:
        os.chdir(relroot)
        with tarfile.open(archive) as zip:
            zip.extractall(dst)
    except:
        if os.path.exists(archive):
            delete(archive)
        if os.path.exists(dst):
            delete(dst)
        raise
    finally:
        os.chdir(pwd)


def dump(self, name):
    for k in self.__dict__.keys():
        if isinstance(self.__dict__[k], (str, list, tuple, dict, bool, int)) and not k.startswith('_'):
            yield '%s.%s = "%s"\n' % (name, k, self.__dict__[k])


def expand_macros(node, vars, extra_vars=None):
    def sub_macro(m):
        type = m.groups()[0]
        expr = m.groups()[1]
        if type == '%':
            expr = 'self.' + expr

        resolved = False
        for var in [vars, extra_vars]:
            try:
                o = eval(expr, {}, {'self': var})
                resolved = True
                break
            except:
                pass
        if not resolved:
            error("'%s' could not be resolved in string '%s'" %
                  (m.groups()[1], node))
        if o is None:
            return ''
        elif isinstance(o, (list, tuple)):
            return ' '.join(o)
        return str(o)

    if hasattr(node, '__dict__'):
        for k, v in node.__dict__.iteritems():
            if not k.startswith('_'):
                node.__dict__[k] = expand_macros(v, vars)
    elif isinstance(node, dict):
        for k, v in node.iteritems():
            node[k] = expand_macros(v, vars)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            node[i] = expand_macros(v, vars)
    elif isinstance(node, str):
        orig_node = node
        iters = 0
        while True:
            v = re.sub('(?<!\\\)([%$]){([^}]+)}',
                       sub_macro, node)
            if v == node:
                break
            iters += 1
            if iters >= 500:
                sys.exit('Too many macro substitutions, possible recursion:'
                         '\'%s\'' % orig_node)
            node = v

    return node


def replace_in_file(filename, word_dic):
    rc = re.compile('|'.join(map(re.escape, word_dic)))

    def translate(match):
        return word_dic[match.group(0)]
    for line in fileinput.FileInput(filename, inplace=1):
        print rc.sub(translate, line)


def run(cmd, args, cwd, env=None):
    trace('@%s %s' % (cmd, args))

    if not isinstance(cmd, str):
        error('cmd argument must be a string')
    if not isinstance(args, list):
        error('args argument must be a list')
    cmd_list = [cmd] + args
    try:
        proc = subprocess.Popen(cmd_list, shell=False, cwd=cwd,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        error(e)

    stdout, stderr = proc.communicate()
    exit_code = proc.returncode

    if not exit_code == 0:
        raise CommandException('"%s" failed, error code %s\nstderr:\n%s' % (
            cmd, exit_code, stderr), cwd=cwd)

    return (exit_code, stdout, stderr)


def run_shell(cmd, print_cmd=False, cwd=None):
    if print_cmd:
        print '++', cmd
    if not print_cmd:
        trace(cmd)
    proc = subprocess.Popen(cmd, shell=True, bufsize=-1, cwd=cwd)
    exit_code = proc.wait()
    if not exit_code == 0:
        raise CommandException('"%s" failed, error code %s' %
                               (cmd, exit_code), cwd)


def backtick(cmd, print_cmd=False, echo=False):
    if print_cmd or echo:
        print '``', cmd
    if not print_cmd:
        trace('``' + cmd)
    proc = subprocess.Popen(cmd, shell=True, bufsize=-1,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate()

    exit_code = proc.returncode

    if echo:
        info(stdout)
        if len(stderr) > 0:
            warn(stderr)

    if not exit_code == 0:
        raise CommandException('"%s" failed, error code %s\nstderr:\n%s' % (
            cmd, exit_code, stderr), os.getcwd())

    return stdout.split('\n')


def get_host():
    search_paths = ['/usr/share', '/usr/local/share']
    am_config_guess = []
    for path in search_paths:
        am_config_guess.extend(glob.glob(os.path.join(
            path, os.path.join('automake*', 'config.guess'))))
    for config_guess in am_config_guess:
        config_sub = os.path.join(os.path.dirname(config_guess), 'config.sub')
        if os.access(config_guess, os.X_OK) and os.access(config_sub, os.X_OK):
            return backtick('%s %s' % (config_sub, backtick(config_guess)[0]))[0]
    return 'python-%s' % os.name


def get_cpu_count():
    try:
        return os.sysconf('SC_NPROCESSORS_CONF')
    except:
        return 1
