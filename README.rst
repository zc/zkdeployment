==============================================
ZooKeeper-based automated deployment framework
==============================================

We want "push-button" fully-automated deployment based on a high-level
service-oriented model.  We've decided to use trees as that high-level
model.

Here's an example::

  /who
    /myfoo : foo
      version = 1.0
      fuzz = 1
      /providers
      /deploy
        /0015C5F4D4F0

A tree has a root node, which may have child nodes, which may have
child notes, and so on.

Nodes have properties.

There's a text representation that let's you model certain high-level
sematics, suct as node types, symbolic links, and property links.  See
`zc.zk <http://pypi.python.org/pypi/zc.zk>`_ for details.

In the example above, there's a node named ``myfoo`` of type ``foo``
with ``version`` and ``fuzz`` properties.  It's a child node of
``who``. Network services typically have ``providers`` sub-nodes where
instances of the service register themselves in ZooKeeper.

Note that types are just properties.  We provide a short-hand for
expressing types on the same line as the node with a ':' in between
the node name and the type.

Deployable objects, typically network services, have ``deploy``
sub-nodes with sub-nodes indicating the intent to deploy the component
on a host or on all hosts with a given *role* [#role]+.  The name of
the deploy sub-node is a host or role indentifier.  A deploy sub-node
may also have an ``n`` property saying how many deployments/instances
to deploy on the node.

A node type consists of a name, which names an RPM.  The name
identifies an RPM and corresponsing directories in ``/opt`` and
``/etc``

Tree Management
===============

The canonical representation of the tree will consist of textual
representations checked into a version control system (VCS).
Typically, the tree will be split into multiple files, one for each
top-level node in the tree that is managed by this process. [#unmanaged]_.

There will be one or more agents that poll the VCS and update the tree
when there is a relevent change.  These may be long-running processes
or cron jobs.  A ZooKeeper-based locking strategy will be used to
avoid duplicate updates.  The revision number will be stored in a node
of the tree after the update.

Host agents
===========

Agents will run on each *host* [#host]_.

Host agents are long-running processes that watch the ZooKeeper tree
node containing a revision number and update the host based on data in
the tree whenever the tree (revision) changes.

Whenever the tree revision changes, the host agent:

- determines its role or machine identifier (well probably on startup)

- scans the tree for any deployments for the host (or role).

- scans /opt for applications and /etc for application deployments.

  A deployment is recorded in a file in a directory named after the
  application in /etc.  The file name is the path of the node with the
  leading slash removed and slashes converted to colons, plus a period
  and a deployment number.  For example, the first deployment of
  ``/who/myfoo`` in the example above would be recorded in
  ``/etc/foo/who:myfoo.0.deployed``.

- If there are application deployments that aren't reflected in the
  tree, they are uninstalled.

  This will be done by invoking::

    /opt/NAME/zookeeper-deploy -u PATH NUMBER

  where

  NAME
     The deployment type name.

  PATH
     The deployment path, which is the path at which the deployed node
     was found in the tree. (Note that that node might not be in the
     tree any more, or might not have the same type.)

  NUMBER is the deployment number, 0, 1, ..., depending on the number
     of deployments on the host.

- If there are no deployments in the tree for an application, the
  application is uninstalled.

  This entails:

  1. Uninstalling the RPM.

  2. Removing the application directory in /opt the RPM uninstall
     didn't do it (e.g. if it was patched.)

- For all deployments found in the tree, the agent gets the type name
  and:

  - Installs the corresponding RPM, if necessary.

  - Installs patches, if necessary.

  - runs the script ``/opt/NAME/zookeeper-deploy`` with the deployment
    path and number.

    In the example above, on ``app.example.com``, we'll run::

      /opt/foo/zookeeper-deploy bar /who/myfoo 0

    If the script exits with a non-zero exit code, the agent will get
    really pissed and complain to someone and a human will have to get
    involved.  Any output will be emailed to a configured address (ala
    cron).

Application versions
====================

Applications must have versions.  There are basically 2 approaches
used:

1. The application version is recorded in the RPM version.  In this
   case, only one version of an application can be installed at once
   on a host.

   The version to be deployed is included as a property of the
   application, as in the earlier example.

2. The version is included in the rpm name.  Multiple versions can be
   installed at the same time, because they're separate RPMs.

   There can only be one version of the RPM. When we want a new
   version, we create a new RPM.

   The version is not allowed to include dashes.  The agent will split
   the type on the last dash to get the application name.


Hosts and host versions
=======================

Changes take time. This is kind of obvious, yet easy to forget.  At a
minimum, we need visibility to this.  A tree will have a top-level
``hosts`` node that contains nodes for each host.  The hosts node will
contain the current version of the tree in subversion.  Each host node
will have a version that reflects the version the host has been synced
with.  So, over time, you might start with::

    /hosts
      version = 1111
      /0015C5F4D4F0
         name = 'app.example.com:12345'
         version = 1111
      /0015C5F41234
         role = 'database server'
         version = 1111

Each host will be identified by a unique identifier. These identifiers
aren't very human friendly.  A Host may have a name property or a role
property (or both).  If it has a role, it will be configured based
**solely** on it's role.  If it doesn't have a role, it can be
configured either by it's name or by it's id.  Both the id and name
must be unique.

Now we update the cluster::

    /hosts
      version = 1112
      /0015C5F4D4F0
         name = 'app.example.com:12345'
         version = 1111
      /0015C5F41234
         role = 'database server'
         version = 1111

At this point, the hosts are out of sync.

After a little while::

    /hosts
      version = 1112
      /0015C5F4D4F0
         name = 'app.example.com:12345'
         version = 1112
      /0015C5F41234
         role = 'database server'
         version = 1111

Finally::

    /cluster
      version = 1112
      /0015C5F4D4F0
         name = 'app.example.com:12345'
         version = 1112
      /0015C5F41234
         version = 1112
         role = 'database server'

And we're done.

A problem is that you don't want to update the tree while a host is
syncronizing.  We probably want a locking mechanism to prevent
updating the tree from VCS while workers are reading it.

And don't forget that this doesn't eliminate thought. :)

Updates
=======

We support of several flavors of updates:

- In-place rolling

  The service can tolerate updates while it's running, and it can
  tolerate different versions running at the same time.

- Non-in-place rolling

  The service cannot tolerate updates while it's running, but it can
  tolerate different versions running at the same time..

- Split

  The service cannot tolerate updates while it's running, and it can't
  have multiple versions in service at once.

- Patch

  We need to patch existing deployments for minor changes.  Modifying
  patches files doesn't cause run-time problems and the software can
  tolerate patched and unpatched versions running at the same time.

inplace rolling upgrades
------------------------

The service can tolerate updates while it's running.

It's OK to have 2 versions in service at once, so we can do rolling
restarts.

Consider a run-time tree with some providers::

   /who
     /myfoo : foo bar
       version = '1.1'
       fuzz = 1
       /providers
         /app.example.com:12345
            pid = 1000
         /app.example.com:12346
            pid = 1001
       /deploy
         /app.example.com
            n = 2

Note that we record a version in the node.  All of the providers
are up to date.  It's up to the deployment scripts to record
deployed versions.

We update the node version::

   /who
     /myfoo : foo bar
       version = '1.4'
       fuzz = 1
       /providers
         /app.example.com:12345
            pid = 1000
         /app.example.com:12346
            pid = 1001
       /deploy
         /app.example.com
            n = 2

We **automatically** take the following steps:

- Update the foo rpm to version 1.4

- Call /opt/foo/bin/zookeeper-deploy /who/myfoo 0

  This will update the first deployment, updating configuration, as
  necessary and restarting any processes that the instance defines.

  The script *should* wait for the app to be up and running before
  it returns.

  "Up and running" should probably entail both waiting for it to be
  registered and satisfying an operational test, like satisfying a
  web request.

- Call /opt/foo/bin/zookeeper-deploy /who/myfoo 1

non-in-place rolling updates
----------------------------

The service cannot tolerate updates while it's running.

It's OK to have 2 versions in service at once, so we can do rolling
restarts.

Consider a run-time tree with some providers::

  /who
    /myfoo : foo-1.1 bar
      fuzz = 1
      /providers
        /app.example.com:12345
           pid = 1000
        /app.example.com:12346
           pid = 1001
      /deploy
        /app.example.com
           n = 2

We update the node version::

  /who
    /myfoo : foo-1.4 bar
      fuzz = 1
      /providers
        /app.example.com:12345
           pid = 1000
        /app.example.com:12346
           pid = 1001
      /deploy
        /app.example.com
           n = 2

We **automatically** take the following steps:

- install foo-1.4

- Call /opt/foo-1.4/bin/zookeeper-deploy /who/myfoo 0

  This will update the first deployment, updating configuration, as
  necessary and restarting any processes that the instance defines.

  The script *should* wait for the app to be up and running before
  it returns.

- Call /opt/foo/bin/zookeeper-deploy /who/myfoo 1

Split updates
-------------

The service cannot tolerate updates while it's running.

It's **not** OK to have 2 versions in service at once, so we
**cannot simply** do rolling restarts.  Note that we can't assure
that 2 versions aren't in service at once without taking down time,
so this may be somewhat relative.

This approach assummes that there is a consumer of the
application's providers that is version aware.  Let's assume for
the sake of argument that this is a load balancer.

Consider a run-time tree with some providers::

   /who

     /lb
       /backend
         providers = ../../myfoo/providers

     /myfoo : foo-1.1 bar
       fuzz = 1
       /providers
         /app1.example.com:12345
            pid = 1000
            version = '1.1'
         /app1.example.com:12346
            pid = 1001
            version = '1.1'
         /app2.example.com:12345
            pid = 1000
            version = '1.1'
         /app2.example.com:12346
            pid = 1001
            version = '1.1'
       /deploy
         /app1.example.com
            n = 2
         /app2.example.com
            n = 2

The load-balancer is smart. :)

- It doesn't use all of the providers.
- It only uses providers with the version that the
  majority of providerd have.
- It also has some intertia, meaning that it knows the version it
  used last and won't switch to a new version until a different
  version has 60% of the providers.

We update the node version::

   /who

     /lb : smartypants
       /backend
         providers = ../../myfoo/providers

     /myfoo : foo-1.4 bar
       fuzz = 1
       /providers
         /app1.example.com:12345
            pid = 1000
            version = '1.1'
         /app1.example.com:12346
            pid = 1001
            version = '1.1'
         /app2.example.com:12345
            pid = 1000
            version = '1.1'
         /app2.example.com:12346
            pid = 1001
            version = '1.1'
       /deploy
         /app1.example.com
            n = 2
         /app2.example.com
            n = 2

We **automatically** take the following steps on each node::

  install foo-1.4
  for i in range(2):
        /opt/foo-1.4/bin/zookeeper-deploy /who/myfoo $i


When the first few instances are restarted, the lb will ignore
them.  When enough instances are running the new version, the lb
will switch to them and ignore the old ones.

An alternative to a smart consumer is a smart agent that filters an
input providers node into an output providers node.

If there are numerous consumers, we have to worry about consumers
having a different idea of what the majority is.

Maybe we'll fix our apps that provoke this case so we don't have to
implement it. :)

Parallelization
---------------

Each agent works independently.  When a ``/hosts`` version changes,
host agents will fire on each host in the cluster.

For availability reasons, we don't want to restart all instances at
once.  For now, we'll be conservative and only restart one instance
for a node at once.  To do this, for each application node, we'll have
a lock.  A host gets the node's lock before updating the node.

If an agent can't get the lock for a node, it will try to get a lock
for another node (for which it has updates), and so on.  The agent
will never hold more than one lock at a time.

Error Handling
==============

Errors happen:

- named rpms don't exist

- deployment scripts fail.

- The tree has errors, like two apps requiring two versions of the same
  rpm.

What should happen in this case? Should we try to recover to a
known good state? Or should we ask for human assistence?

I wonder what the book I'm reading says about this. :)

One thing we should do is to try to fail early:

- Check for rpm inconsistencies in tree.

  Do nothing if error.

- Do rpm updates before doing any configurations.

  Revert rpms and do nothing if errors.

- Use a dry-run uption (to be added) before making any changes.

Drift
=====

We have to decide if drift is allowed and, if it is, how it will be
managed.  I suspect some buildout updates will be needed to do this
well.

Errors
======

How should we deal with deployment errors?

Cause a zimagent alert?  Send an email?


Changes
=======

0.12.0 (2013-11-27)
-------------------

- Added sync from git.

0.11.1 (2013-08-28)
-------------------

- After running **stage-build**, fix the permissions of the built
  software to ensure all users can read the files and directories
  installed.  (Necessary to deal with sdists that include EGG-INFO with
  overly restrictive permissions.)


0.11.0 (2013-05-06)
-------------------

- Generalized agent VCS (stage) support.

  Git is now supported via versions of the form:

  git://REPO#VERSION

0.10.0 (2013-04-05)
-------------------

- If a deployment fails, record the error in the host's
  properties. This makes it easier to see which node in a cluster
  failed.

- Don't bother to restart zimagent any more. It's not necessary.


0.9.10 (2012-12-20)
-------------------

- Fixed: Failed to take yum's stupidity into account.  The yum
  install command won't install a version lower than what's
  installed. zkdeployment now tries the downgrade command if install
  fails and something is installed.

- Fixed: When the ``/hosts`` version was None, the syncronizer would
  still syncronize, clearing the error condition.

0.9.9 (2012-12-14)
------------------

- If there is a deployment failure, deployment is halted cluster wide.

  (This is indicated by setting the cluster version to None.)

0.8.3 (2012-11-30)
------------------

- Fixed more: the agent failed when the HOME environment variable wasn't
  set.

0.8.2 (2012-11-29)
------------------

- Fixed: the agent failed when the HOME environment variable wasn't
  set.

- Fixed: the agent sometimes didn't shut down ZooKeeper sessions
  cleanly, leading to spurious "agent is already running" errors on
  restart.

- RPM fixed: the service status command didn't return a non-zero exit
  status when the service wasn't running.


0.8.1 (2012-11-27)
------------------

Mercural fail. Never mind.

0.8.0 (2012-11-07)
------------------

- The /hosts node is now created if it doesn't exist. This is
  important for setting up new clusters.

- sync now recognizes .zkx files, which are imported, without
  trimming, after .zk files are imported.

- The agent script now accepts an option, --assert-zookeeper-address
  (-z) to assert the address expected of 'zookeeper'.  This is useful
  when staging to make sure zkdeployment on a stage machine doesn't
  talk to a production ZooKeeper server.

0.7.1 (2012-10-24)
------------------

- Fixed: Monitoring bug caused spurious alerts


0.7.0 (2012-10-05)
------------------

- Added sub-type support.

- Use up-to-date zookeeper libraries.

0.6.0 (2012-10-04)
------------------

Don't include process configuration in RPM.

0.5.1 (2012-09-25)
------------------

- Added temporary debug logging to debug an intermittent hang.

- Fixed: In "staging" mode, signals weren't handled properly, making
  restarts take too long.

0.5.0 (2012-09-19)
------------------

- Refactored logging  output to get output in real time, rather than
  waiting for sub-processes to finish.

- Don't log at DEBUG logging as ZooKeeper debug logging is too annoying.

0.4.1 (2012-09-06)
------------------

- Fixed: Changing value of svn_location results in failure
  https://bitbucket.org/zc/zkdeployment/issue/1

- Fixed: on sync, there was spurious output about not deleting
  ephemeral nodes.

0.4.0 (2012-09-05)
------------------

- The host agent now runs stage-build scripts in the script's directory so
  the scripts don't have to.

- Added an unmonitored mode.  If zimagent isn't around, then don't
  register act as a zim monitor.

0.3.1 (2012-08-29)
------------------

- Fixed: Clean up of non-empty etc directories caused convergence to
  fail.

  https://bitbucket.org/zc/zkdeployment/issue/2

- Fixed: install of rpms with versions in names not handled correctly

  https://bitbucket.org/zc/zkdeployment/issue/3

0.3.0 (2012-08-11)
------------------

- Set HOME to /root if needed.

- Fixed: application property links didn't work, making it hard to be
  DRY in some situations.

0.2.1 (2012-08-08)
------------------

Fixed: Legacy non-ephemeral host nodes weren't handled correctly.  (No test :()

0.2.0 (2012-08-07)
------------------

- check that rpm is actually installed by checking installed version.

  amongst the many way that yum sucks is that when it can't find a
  requested package, it exits with a 0 exit status.

- Agent record fqdn and role in zk

- Make host nodes ephemeral.  Log host version
  to disk and load it on startup. /etc/zim/host-version

- Don't fail of there isn't an /etc/APP directory.

  Make one if there isn't one prior to calling zookeeper-deploy.

- Lists of installed RPMs weren't parsed correctly, leading to spurious
  re-installs.


- Removed the agent timeout logic:

  - It's problematic to time tings out at this level.  It's probably
    better to handle this through monitoring.

  - Testing it is slow.

0.1.0 (2012-07-20)
------------------

Initial release


.. [#roles] Roles refer to collections of identically-configured
   machines.  In this model, a host can only have one role.  Hosts in
   role groups are highly despicable and will be created and destroyed
   via automated processes such as AWS autoscaling.

.. [#unmanaged] There may be parts of the tree that aren't managed by
   this process.

.. [#host] A host is an individial real or virtual computer.  A host
   may be a machine or a member of a machine group.  A host will have
   a host identifier that is independent of it's IP.
