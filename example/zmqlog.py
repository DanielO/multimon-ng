#!/usr/bin/env python

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
import sqlite3
import zmq

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--db', type = str, help = 'SQLite3 DB to write to')
    parser.add_argument('-z', '--zmq', type = str, help = 'ZMQ to listen for', required = True)
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
        c.execute('''
CREATE TABLE IF NOT EXISTS frequencies (
    freq    REAL,
    name    TEXT UNIQUE
);
''')
    ctx = zmq.Context.instance()
    listener = ctx.socket(zmq.SUB)
    listener.connect(args.zmq)
    listener.setsockopt(zmq.SUBSCRIBE, b'')

    while True:
        page = listener.recv_json()
        if args.db == None:
            print(page)
        else:
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
                print('Unknown page type ' + page['type'])

if __name__ == '__main__':
    main()
