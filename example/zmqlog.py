#!/usr/bin/env python

import argparse
import zmq

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-z', '--zmq', type = str, help = 'ZMQ to listen for', required = True)
    args = parser.parse_args()

    ctx = zmq.Context.instance()
    listener = ctx.socket(zmq.SUB)
    listener.connect(args.zmq)
    listener.setsockopt(zmq.SUBSCRIBE, b'')

    while True:
        print(listener.recv_json())

if __name__ == '__main__':
    main()
