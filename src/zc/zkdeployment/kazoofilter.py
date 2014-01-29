# kazoo filter is way too chatty.  There's a pull request to fix that,
# but in the mean time, let's filter the noise.
import logging
import sys

discards = 'Sending request(', 'Received response('

class Filter:
    """
    zc.zk.__init__ installs this filter. We'll just make sure it works

    >>> logger = logging.getLogger('kazoo.client')
    >>> logger.setLevel(logging.INFO)
    >>> handler = logging.StreamHandler(sys.stdout)
    >>> logger.addHandler(handler)

    >>> logger.info('connected')
    connected
    >>> logger.info('Sending request(xid=118082): ...')
    >>> logger.info('Received response(xid=118082): ...')
    >>> logger.info('disconnected')
    disconnected

    >>> logger.setLevel(logging.NOTSET)
    >>> logger.removeHandler(handler)

    """

    def filter(self, record):
        message = record.getMessage()
        for discard in discards:
            if discard in message:
                return False
        return True
