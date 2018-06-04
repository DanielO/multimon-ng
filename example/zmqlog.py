#!/usr/local/bin/python2.7

# Sample SQL
#
### Insert names into frequency table
# INSERT INTO frequencies VALUES(148812500.0,'SAGRN');
# INSERT INTO frequencies VALUES(148562500.0,'Hutchison F1');
# INSERT INTO frequencies VALUES(148637500.0,'Hutchison F2');
# INSERT INTO frequencies VALUES(148662500.0,'Hospital');
#
### List most recent 20 pages with frequency name, page type, etc
# SELECT pages.captime, frequencies.name, pages.type, pocsag_pages.ptype, pocsag_pages.address, flex_pages.capcode, pages.msg FROM frequencies, pages LEFT OUTER JOIN pocsag_pages ON pages.id == pocsag_pages.pid LEFT OUTER JOIN flex_pages ON pages.id == flex_pages.pid WHERE frequencies.freq == pages.chfreq ORDER BY pages.captime DESC LIMIT 20;
#
### As above but with the text 'MFS' in the message
# SELECT pages.captime, frequencies.name, pages.type, pocsag_pages.ptype, pocsag_pages.address, flex_pages.capcode, pages.msg FROM frequencies, pages LEFT OUTER JOIN pocsag_pages ON pages.id == pocsag_pages.pid LEFT OUTER JOIN flex_pages ON pages.id == flex_pages.pid WHERE frequencies.freq == pages.chfreq AND pages.id IN (SELECT pid FROM pages_fts WHERE msg MATCH 'mfs') ORDER BY pages.captime DESC LIMIT 20;

import argparse
import daemon
import daemon.pidfile
import logging
import logging.handlers
import sqlite3
import sys
import zmq

class NullContextManager(object):
    def __init__(self, dummy_resource=None):
        self.dummy_resource = dummy_resource
    def __enter__(self):
        return self.dummy_resource
    def __exit__(self, *args):
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--db', type = str, help = 'SQLite3 DB to write to')
    parser.add_argument('-z', '--zmq', type = str, help = 'ZMQ to listen for', required = True)
    parser.add_argument('-L', '--log', type = str, help = 'Log file (will cause it to daemonise)')
    parser.add_argument('-P', '--pidfile', type = str, help = 'PID file (only used with --log)')
    args = parser.parse_args()

    if args.db != None:
        db = sqlite3.connect(args.db)
        c = db.cursor()
        c.execute('CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts4(pid, msg);')
        c.execute('''
CREATE TABLE IF NOT EXISTS pages(
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    type    TEXT,
    chfreq  REAL,
    captime INTEGER,
    msg     TEXT
);
''')
        c.execute('CREATE INDEX IF NOT EXISTS pages_id ON pages(id)')
        c.execute('CREATE INDEX IF NOT EXISTS pages_captime ON pages(captime)')
        c.execute('''
CREATE TABLE IF NOT EXISTS pocsag_pages(
    pid     INTEGER,
    rate    INTEGER,
    address INTEGER,
    func    INTEGER,
    ptype   TEXT,

    FOREIGN KEY(pid) REFERENCES pages(id)
);
''')
        c.execute('CREATE INDEX IF NOT EXISTS pocsag_pages_id ON pocsag_pages(pid)')
        c.execute('''
CREATE TABLE IF NOT EXISTS flex_pages(
    pid     INTEGER,
    msgtime INTEGER,
    baud    INTEGER,
    level   INTEGER,
    phaseno CHAR(1),
    cycleno INTEGER,
    frameno INTEGER,
    capcode INTEGER,

    FOREIGN KEY(pid) REFERENCES pages(id)
);
''')
        c.execute('CREATE INDEX IF NOT EXISTS flex_pages_id ON flex_pages(pid)')
        c.execute('''
CREATE TABLE IF NOT EXISTS frequencies (
    freq    REAL,
    name    TEXT UNIQUE
);
''')

    # Configure logging
    global logger
    logger = logging.getLogger('zmqlog')
    logger.setLevel(logging.DEBUG)

    if args.log != None:
        lh = logging.handlers.WatchedFileHandler(args.log)
    else:
        lh = logging.StreamHandler()

    lh.setFormatter(logging.Formatter('%(asctime)s %(name)s:%(levelname)s: %(message)s', '%Y/%m/%d %H:%M:%S'))
    logger.addHandler(lh)

    daemon_context = daemon.DaemonContext()
    daemon_context.files_preserve = [lh.stream]

    # Daemonise if we have a log file
    if args.log != None:
        pidfile = None
        if args.pidfile != None:
            pidfile = daemon.pidfile.PIDLockFile(args.pidfile)

        ctxmgr = daemon.DaemonContext(pidfile = pidfile, files_preserve = [lh.stream])
    else:
        ctxmgr = NullContextManager()

    with ctxmgr:
        try:
            dologging(args.zmq, args.db)
        except sqlite3.Error as e:
            logger.error("SQL error: " + str(e.args[0]))
        except sqlite3.OperationalError as e:
            logger.error("SQL operataional error: " + str(e.args[0]))
        except:
            e = sys.exc_info()[0]
            logger.error('Exception: ' + str(e))

    logger.error('Exiting')

def dologging(zmqname, dbname):
    logger.error('zmqlog starting')
    ctx = zmq.Context.instance()
    listener = ctx.socket(zmq.SUB)
    listener.connect(zmqname)
    listener.setsockopt(zmq.SUBSCRIBE, b'')

    # Re-open DB because daemon will close the file handle
    if dbname != None:
        db = sqlite3.connect(dbname)
        c = db.cursor()
    while True:
        page = listener.recv_json()
        logger.info(page)
        if db != None:
            if page['type'] == 'POCSAG':
                c.execute('INSERT INTO pages(type, chfreq, captime, msg) VALUES (?, ?, datetime(?), ?)',
                              (page['type'], page['chfreq'], page['capts'], page['msg']))
                pid = c.lastrowid
                c.execute('INSERT INTO pocsag_pages(pid, rate, address, func, ptype) VALUES (?, ?, ?, ?, ?)',
                              (pid, page['rate'], page['address'], page['function'], page['ptype']))
                c.execute('INSERT INTO pages_fts(pid, msg) VALUES (?, ?)', (pid, page['msg']))
                db.commit()
            elif page['type'] == 'FLEX':
                c.execute('INSERT INTO pages(type, chfreq, captime, msg) VALUES (?, ?, datetime(?), ?)',
                              (page['type'], page['chfreq'], page['capts'], page['msg']))
                pid = c.lastrowid
                c.execute('INSERT INTO flex_pages(pid, msgtime, baud, level, phaseno, cycleno, frameno, capcode) VALUES (?, datetime(?), ?, ?, ?, ?, ?, ?)',
                              (pid, page['msgts'], page['baud'], page['level'], page['phaseno'], page['cycleno'], page['frameno'], page['capcode']))
                c.execute('INSERT INTO pages_fts(pid, msg) VALUES (?, ?)', (pid, page['msg']))
                db.commit()
            else:
                logger.error('Unknown page type ' + page['type'])

if __name__ == '__main__':
    main()
