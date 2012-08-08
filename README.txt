***********************
Title Here
***********************

Changes
*******

0.2.2 (2012-08-07)
==================

Fixed: Legacy non-ephemeral host nodes weren't handled correctly.  (No test :()

0.2.0 (2012-08-07)
==================

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
==================

Initial release
