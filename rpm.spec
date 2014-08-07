Name: zkdeployment
Version: 0
Release: 1

Summary: ZooKeeper Deployment
Group: Applications/ZIM
Requires: cleanpython27
Requires: nagios-plugin-check-fileage
Requires: sbo
BuildRequires: cleanpython27
%define python /opt/cleanpython27/bin/python

##########################################################################
# Lines below this point normally shouldn't change

%define source %{name}-%{version}

Vendor: Zope Corporation
Packager: Zope Corporation <sales@zope.com>
License: ZPL
AutoReqProv: no
Source: %{source}.tgz
Prefix: /opt
BuildRoot: /tmp/%{name}

%description
%{summary}

%prep
%setup -n %{source}

%build
rm -rf %{buildroot}
mkdir %{buildroot} %{buildroot}/opt
cp -r $RPM_BUILD_DIR/%{source} %{buildroot}/opt/%{name}
%{python} %{buildroot}/opt/%{name}/install.py bootstrap
%{python} %{buildroot}/opt/%{name}/install.py buildout:extensions=
%{python} -m compileall -q -f -d /opt/%{name}/eggs  \
   %{buildroot}/opt/%{name}/eggs \
   > /dev/null 2>&1 || true
rm -rf %{buildroot}/opt/%{name}/release-distributions

# Gaaaa! buildout doesn't handle relative paths in egg links. :(
sed -i s-/tmp/%{name}-- \
   %{buildroot}/opt/%{name}/develop-eggs/zc.%{name}.egg-link 
%clean
rm -rf %{buildroot}
rm -rf $RPM_BUILD_DIR/%{source}

%pre
rm -rf /opt/%{name}/eggs/setuptools*

%post
if [ "$1" = "1" ]; then
cat <<EOF >/etc/zim/agent.d/zksync.cfg
[zksync]
class = zim.nagiosplugin.Monitor
interval = 180
/zkdeployment/sync =
    /usr/bin/check-fileage -e -f /usr/share/zkdeployment/tombstone -w 3 -c 20
EOF
fi

%preun
if [ "$1" = "0" ]; then
    rm /etc/zim/agent.d/zksync.cfg
fi
%files
%defattr(-, root, root)
/opt/%{name}
