import os
import re

from bockbuild.package import Package
from bockbuild.util.util import *


class MonoMasterPackage(Package):

    def __init__(self):
        Package.__init__(self, 'mono', None,
                         sources=[os.getenv('MONO_REPOSITORY')
                                  or 'git://github.com/mono/mono.git'],
                         git_branch=os.getenv('MONO_BRANCH') or None,
                         revision=os.getenv('MONO_BUILD_REVISION'),
                         configure_flags=[
                             '--enable-nls=no',
                             '--with-ikvm=yes'
                         ]
                         )
        self.source_dir_name = 'mono'
        # This package would like to be lipoed.
        self.needs_lipo = True

        # Don't clean the workspace, so we can run 'make check' afterwards
        self.dont_clean = True

        if Package.profile.name == 'darwin':
            self.configure_flags.extend([
                '--with-libgdiplus=%s/lib/libgdiplus.dylib' % Package.profile.staged_prefix,
                '--enable-loadedllvm',
                'CXXFLAGS=-stdlib=libc++'
            ])

            self.sources.extend([
                # Fixes up pkg-config usage on the Mac
                'patches/mcs-pkgconfig.patch'
            ])
        else:
            self.configure_flags.extend([
                '--with-libgdiplus=%s/lib/libgdiplus.so' % Package.profile.staged_prefix
            ])

        self.gcc_flags.extend(['-O2'])

        self.configure = './autogen.sh --prefix="%{package_prefix}"'

        self.extra_stage_files = ['etc/mono/config']

    def build(self):
        self.make = '%s EXTERNAL_MCS=%s EXTERNAL_RUNTIME=%s' % (
            self.make, self.profile.env.system_mcs, self.profile.env.system_mono)
        Package.build(self)

    def prep(self):
        Package.prep(self)
        for p in range(1, len(self.local_sources)):
            self.sh('patch -p1 < "%{local_sources[' + str(p) + ']}"')

    def arch_build(self, arch):
        if arch == 'darwin-64':  # 64-bit build pass
            self.local_gcc_flags = ['-m64']
            self.local_configure_flags = ['--build=x86_64-apple-darwin11.2.0']

        if arch == 'darwin-32':  # 32-bit build pass
            self.local_gcc_flags = ['-m32']
            self.local_configure_flags = ['--build=i386-apple-darwin11.2.0']

        self.local_configure_flags.extend(
            ['--cache-file=%s/%s-%s.cache' % (self.profile.build_root, self.name, arch)])

    def install(self):
        Package.install(self)

        registry_dir = os.path.join(
            self.staged_prefix, "etc", "mono", "registry", "LocalMachine")
        ensure_dir(registry_dir)

    def deploy(self):
        if self.profile.arch == 'darwin-universal':
            os.symlink('mono-sgen64', '%s/bin/mono64' % self.staged_profile)
            os.symlink('mono-sgen32', '%s/bin/mono32' % self.staged_profile)

        text = " ".join(open('%s/bin/mcs' % self.staged_profile).readlines())
        regex = os.path.join(self.profile.MONO_ROOT,
                             "Versions", r"(\d+\.\d+\.\d+)")
        match = re.search(regex, text)
        if match is None:
            return
        token = match.group(1)

        trace(token)
        trace(self.package_prefix)
        if self.package_prefix not in match:
            error("%s references Mono %s\n%s" % ('mcs', match, text))

MonoMasterPackage()
