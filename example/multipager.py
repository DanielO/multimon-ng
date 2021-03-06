#!/usr/local/bin/python2.7
# -*- coding: utf-8 -*-
##################################################
# GNU Radio Python Flow Graph
# Title: Multipager
# Generated: Thu Aug  3 14:04:34 2017
##################################################

import numpy
from gnuradio import analog
from gnuradio import audio
from gnuradio import blocks
from gnuradio import eng_notation
from gnuradio import filter as grfilter
from gnuradio import gr
from gnuradio.eng_option import eng_option
from gnuradio.filter import firdes
from gnuradio.filter import pfb

import argparse
import trollius as asyncio
import daemon
import daemon.pidfile
import exceptions
import logging
import logging.handlers
import osmosdr
import re
import subprocess
import sip
import string
import sys
import zmq

# Command to run multimon-ng
# Can't avoid using sox (for unknown reasons multimon-ng doesn't like direct output from GNURadio) so convert from our channel rate to what it wants
cmdpat = "/usr/local/bin/sox -t raw -esigned-integer -b16 -r {audio_in} - -esigned-integer -b16 -r {audio_out} -t raw - | /usr/local/bin/multimon-ng -t raw -q -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -a FLEX -e -u --timestamp -"

pocsagre = re.compile('([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}): POCSAG([0-9]+): Address: +([0-9]+) +Function: +([0-9]+) +([A-Za-z]+): (.*)')

class NullContextManager(object):
    def __init__(self, dummy_resource=None):
        self.dummy_resource = dummy_resource
    def __enter__(self):
        return self.dummy_resource
    def __exit__(self, *args):
        pass

# FLEX supports other pages types, just graph alphanumeric for now
flexre = re.compile('([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}): FLEX: ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}) ([0-9]+)/([0-9]+)/([A-Za-z]) ([0-9]+).([0-9]+) \[([0-9]+)\] ALN (.*)')
def parse_multimon(zmqh, fh, chfreq, proc):
    status = proc.poll()
    if status != None:
        logger.error('Process for %.3f stopped with status %d' % (chfreq / 1e6, status))
        sys.exit(1)
    line = fh.readline().strip()
    if process_pocsag(zmqh, chfreq, line):
        return
    elif process_flex(zmqh, chfreq, line):
        return
    else:
        logger.warning('Unparseable line "%s" for %.3f' % (line), chfreq / 1e6)

def process_pocsag(zmqh, chfreq, line):
    m = pocsagre.match(line)
    if m == None:
        return False
    (capts, rate, address, function, ptype, msg) = m.groups()
    printable = set(string.printable)
    msg = filter(lambda x: x in printable, msg)
    logger.info('%.4f MHz: POCSAG %s %s %s %s: %s' % (chfreq / 1e6, rate, address, function, ptype, msg))
    if zmqh != None:
        zmqh.send_json({
            'chfreq' : chfreq,
            'type' : 'POCSAG',
            'capts' : capts,
            'rate' : int(rate),
            'address' : int(address),
            'function' : int(function),
            'ptype' : ptype,
            'msg' : msg
            })
    return True

def process_flex(zmqh, chfreq, line):
    m = flexre.match(line)
    if m == None:
        return False
    (capts, msgts, baud, level, phaseno, cycleno, frameno, capcode, msg) = m.groups()
    printable = set(string.printable)
    msg = filter(lambda x: x in printable, msg)
    logger.info('%.4f MHz: FLEX %s %s/%s/%s %s.%s [%s] ALN: %s' % (chfreq / 1e6, msgts, baud, level, phaseno, cycleno, frameno, capcode, msg))
    if zmqh != None:
        zmqh.send_json({
            'chfreq' : chfreq,
            'type' : 'FLEX',
            'capts' : capts,
            'msgts' : msgts,
            'baud' : int(baud),
            'level' : int(level),
            'phaseno' : phaseno,
            'cycleno' : int(cycleno),
            'frameno' : int(frameno, 10),
            'capcode' : int(capcode, 10),
            'msg' : msg
            })
    return True

class MultiPager(gr.top_block):
    def __init__(self, freq, ch_width, num_chan, audio_rate, squelch, out_scale, do_audio, zmqh, loop,
                     filename = None, file_samprate = None,
                     osmo_args = None, osmo_freq_cor = None, osmo_rf_gain = None, osmo_if_gain = None, osmo_bb_gain = None):
        gr.top_block.__init__(self, "Multipager")

        sample_rate = num_chan * ch_width
        ##################################################
        # Blocks
        ##################################################
        if not (filename == None) ^ (osmo_args == None):
            raise(exceptions.ValueError('Must specify either filename or osmo_args'))
        if filename != None:
            self.source = blocks.file_source(gr.sizeof_gr_complex*1, filename, True)
            if file_samprate != None and file_samprate < sample_rate:
                raise(exceptions.ValueError('File sample %f rate must be >= computed sample rate %f' % (file_samprate, sample_rate)))
        else:
            self.source = osmosdr.source(args = osmo_args)
            if self.source.set_sample_rate(sample_rate) != sample_rate:
                raise(exceptions.ValueError('Wanted %.4f kSPS got %.4f kSPS' % (sample_rate / 1e3, self.source.get_sample_rate() / 1e3)))
            self.source.set_center_freq(freq, 0)
            if osmo_freq_cor != None:
                self.source.set_freq_corr(osmo_freq_cor, 0)
            self.source.set_dc_offset_mode(0, 0)
            self.source.set_iq_balance_mode(0, 0)
            self.source.set_gain_mode(False, 0)
            if osmo_rf_gain != None:
                self.source.set_gain(osmo_rf_gain, 0)
            if osmo_if_gain != None:
                self.source.set_if_gain(osmo_if_gain, 0)
            if osmo_bb_gain != None:
                self.source.set_bb_gain(osmo_bb_gain, 0)
            self.source.set_antenna("", 0)
            self.source.set_bandwidth(0, 0)

        self.pfb_channelizer_ccf_0 = pfb.channelizer_ccf(
        	  num_chan,
        	  (firdes.low_pass(1.0, sample_rate, 8e3, 1.5e3, firdes.WIN_HAMMING, 6.76)),
        	  1,
        	  60)
        self.pfb_channelizer_ccf_0.set_channel_map(([]))
        self.pfb_channelizer_ccf_0.declare_sample_delay(0)

        ##################################################
        # Connections
        ##################################################
        if file_samprate != None:
            logger.info('Resampling %f' % (file_samprate / sample_rate))
            self.filter = grfilter.fir_filter_ccf(1, firdes.low_pass(
                1, file_samprate, sample_rate / 2, 1e4, firdes.WIN_HAMMING, 6.76))
            self.resampler = grfilter.fractional_resampler_cc(0, file_samprate / sample_rate)
            self.connect((self.source, 0), (self.filter, 0))
            self.connect((self.filter, 0), (self.resampler, 0))
            self.connect((self.resampler, 0), (self.pfb_channelizer_ccf_0, 0))
        else:
            self.connect((self.source, 0), (self.pfb_channelizer_ccf_0, 0))

        # Enable decoding on all channels
        sel = [ True ] * num_chan

        self.fms = {}
        for i in range(num_chan):
            if i > num_chan / 2:
                chfreq = freq +  ch_width * (i - num_chan)
            else:
                chfreq = freq + ch_width * i

            if sel[i]:
                logger.info("Channel %d %.3f MHz" % (i, chfreq / 1e6))
                command = cmdpat.format(audio_in = ch_width, audio_out = audio_rate)
                fm = FMtoCommand(squelch, int(ch_width), 5e3, out_scale, chfreq, command, do_audio = (i == 0) and do_audio)

                self.connect((self.pfb_channelizer_ccf_0, i), (fm, 0))
                self.fms[chfreq] = fm
                loop.add_reader(fm.p.stdout, parse_multimon, zmqh, fm.p.stdout, chfreq, fm.p)
            else:
                n = blocks.null_sink(gr.sizeof_gr_complex*1)
                self.connect((self.pfb_channelizer_ccf_0, i), (n, 0))

class FMtoCommand(gr.hier_block2):
    def __init__(self, squelch, ch_width, max_dev, out_scale, freq, command, do_audio = False):
        gr.hier_block2.__init__(self, "FMtoCommand",
                                    gr.io_signature(1, 1, gr.sizeof_gr_complex),
                                    gr.io_signature(0, 0, gr.sizeof_gr_complex))

        self.analog_pwr_squelch = analog.pwr_squelch_cc(squelch, 1e-4, 0, True)
        self.analog_nbfm_rx = analog.nbfm_rx(
        	audio_rate = ch_width,
            quad_rate = ch_width,
        	tau = 75e-6,
        	max_dev = max_dev,
          )
        self.blocks_float_to_short = blocks.float_to_short(1, out_scale)
        # OSX: if you get Resource Temporarily Unavailable you probably need to increase maxproc, eg
        # sudo launchctl limit maxproc 2000 3000
        logger.debug("Channel %.3f: Starting %s" % (freq / 1e6, str(command)))
        self.p = subprocess.Popen(command, shell = True, stdin = subprocess.PIPE, stdout = subprocess.PIPE)
        self.sink = blocks.file_descriptor_sink(gr.sizeof_short*1, self.p.stdin.fileno())
        self.connect(self, (self.analog_pwr_squelch, 0))
        self.connect((self.analog_pwr_squelch, 0), (self.analog_nbfm_rx, 0))
        self.connect((self.analog_nbfm_rx, 0), (self.blocks_float_to_short, 0))
        self.connect((self.blocks_float_to_short, 0), (self.sink, 0))
        if do_audio:
            self.resampler = grfilter.rational_resampler_fff(
                interpolation = 441,
                decimation = 425,
                taps = None,
                fractional_bw = None)
            self.mult = blocks.multiply_const_vff((0.2, ))
            self.audio_sink = audio.sink(22050, '', True)
            self.connect((self.analog_nbfm_rx, 0), (self.resampler, 0))
            self.connect((self.resampler, 0), (self.mult, 0))
            self.connect((self.mult, 0), (self.audio_sink, 0))

def main():
    parser = argparse.ArgumentParser(formatter_class = argparse.RawDescriptionHelpFormatter, epilog =
    '''Read from a file or sample using the Osmocom SDR interface and decode pager channels.
Splits the sampled data into equal 25kHz segments and does FM decoding
then uses sox to resample to 22050Hz and pass to multimon-ng.
When using an RTL-SDR you need to pick a number of channels that results in a valid sample rate of..
240000 300000 960000 1152000 1200000 1440000 1600000 1800000 1920000 2400000 SPS
For 25kHz channels this means one of 12 48 64 72 96
Sample usage:
  Read from samples.raw (882kSPS) acquired at a centre frequency of 148.6625MHz and process 35 channels
    multipager.py -s samples.raw -R 882000 -f 148662500 -c 35
  Sample 35 channels from a HackRF at 148.6625MHz with 0dB RF gain, 34dB IF gain, 44dB BB gain, 10 PPM correction
    multipager.py -a hackrf -f 148662500 -c 35 -r 0 -l 34 -g 44 -p 10
  Sample 12 channels from am RTL-SDR at 148.6625MHz with 65dB of total gain and play channel 0 as audio
    multipager.py -a rtl -f 148662500 -c 12 -r 65 -n
  Sample 12 channels from am RTL-SDR at 148.6625MHz with 65dB of total gain and emit ZMQ events for decoded pages
    multipager.py -a rtl -f 148662500 -c 12 -r 65 -z 'tcp://127.0.0.1:9000'
''')
    parser.add_argument('-f', '--frequency', type = float, help = 'Centre frequency to tune to', required = True)
    parser.add_argument('-c', '--channels', type = int, help = 'Number of channels at 25kHz each to sample for', required = True)
    parser.add_argument('-a', '--args', type = str, help = 'Osmocom SDR arguments')
    parser.add_argument('-s', '--samplefile', type = str, help = 'Process file of complex samples')
    parser.add_argument('-R', '--filerate', type = int, help = 'Sample rate of file (SPS)')
    parser.add_argument('-p', '--ppm', type = float, help = 'PPM error to correct for when using Osmocom SDR')
    parser.add_argument('-r', '--rfgain', type = int, help = 'RF gain for Osmocom SDR')
    parser.add_argument('-g', '--bbgain', type = int, help = 'Baseband gain for Osmocom SDR')
    parser.add_argument('-l', '--ifgain', type = int, help = 'IF gain for Osmocom SDR')
    parser.add_argument('-q', '--squelch', type = float, help = 'Squelch level (dB) for detection', default = -20)
    parser.add_argument('-n', '--audio', action = 'store_true', help = 'Enable audio on channel 0', default = False)
    parser.add_argument('-o', '--outscale', type = int, help = 'Amount to scale output by', default = 10000)
    parser.add_argument('-z', '--zmq', type = str, help = 'Bind to this ZMQ and send messages')
    parser.add_argument('-L', '--log', type = str, help = 'Log file (will cause it to daemonise)')
    parser.add_argument('-P', '--pidfile', type = str, help = 'PID file (only used with --log)')

    args = parser.parse_args()
    if not (args.args == None) ^ (args.samplefile == None):
        parser.error('Must have Osmocom SDR arguments or samplefile')

    if args.samplefile != None and args.filerate == None:
        parser.error('Must specify sample rate when using file')

    # Configure logging
    global logger
    logger = logging.getLogger('multipager')
    logger.setLevel(logging.INFO)

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
        if args.pidfile != None:
            pidfile = daemon.pidfile.PIDLockFile(args.pidfile)

        ctxmgr = daemon.DaemonContext(pidfile = pidfile, files_preserve = [lh.stream])
    else:
        ctxmgr = NullContextManager()

    with ctxmgr:
        try:
            multipager(args)
        except:
            e = sys.exc_info()[0]
            logger.error('Exception: ' + str(e))
    logger.error('Exiting')

def multipager(args):
    logger.warning('Starting multipager')
    if args.zmq != None:
        ctx = zmq.Context.instance()
        zmqh = ctx.socket(zmq.PUB)
        zmqh.bind(args.zmq)
    else:
        zmqh = None

    ch_width = 25e3
    audio_rate = 22.05e3

    loop = asyncio.get_event_loop()

    tb = MultiPager(args.frequency,
                        ch_width,
                        args.channels,
                        audio_rate,
                        args.squelch,
                        args.outscale,
                        args.audio,
                        zmqh,
                        loop,
                        filename = args.samplefile,
                        file_samprate = args.filerate,
                        osmo_args = args.args,
                        osmo_freq_cor = args.ppm,
                        osmo_rf_gain = args.rfgain,
                        osmo_if_gain = args.ifgain,
                        osmo_bb_gain = args.bbgain,
                        )
    tb.start()
    try:
        loop.run_forever()
    finally:
         loop.close()
    tb.stop()
    tb.wait()

if __name__ == '__main__':
    main()
