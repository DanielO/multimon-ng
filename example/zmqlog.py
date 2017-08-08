#!/usr/bin/env python

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
        c.execute('''
CREATE TABLE IF NOT EXISTS pages(
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    type    TEXT,
    chfreq  REAL,
    captime INTEGER
);
''')
        c.execute('''
CREATE TABLE IF NOT EXISTS pocsag_pages(
    pid     INTEGER,
    rate    INTEGER,
    address INTEGER,
    func    INTEGER,
    ptype   TEXT,
    msg     TEXT,

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
    msg     TEXT,

    FOREIGN KEY(pid) REFERENCES pages(id)
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
                c.execute('INSERT INTO pages(type, chfreq, captime) VALUES (?, ?, datetime(?))',
                              (page['type'], page['chfreq'], page['capts']))
                c.execute('INSERT INTO pocsag_pages(pid, rate, address, func, ptype, msg) VALUES (?, ?, ?, ?, ?, ?)',
                              (c.lastrowid, page['rate'], page['address'], page['function'], page['ptype'], page['msg']))
                db.commit()
            elif page['type'] == 'FLEX':
                c.execute('INSERT INTO pages(type, chfreq, captime) VALUES (?, ?, datetime(?))',
                              (page['type'], page['chfreq'], page['capts']))
                c.execute('INSERT INTO flex_pages(pid, msgtime, baud, level, phaseno, cycleno, frameno, capcode, msg) VALUES (?, datetime(?), ?, ?, ?, ?, ?, ?, ?)',
                              (c.lastrowid, page['msgts'], page['baud'], page['level'], page['phaseno'], page['cycleno'], page['frameno'], page['capcode'], page['msg']))
                db.commit()
            else:
                print('Unknown page type ' + page['type'])

if __name__ == '__main__':
    main()
