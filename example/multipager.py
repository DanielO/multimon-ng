#!/usr/bin/env python
# -*- coding: utf-8 -*-
##################################################
# GNU Radio Python Flow Graph
# Title: Multipager
# Generated: Thu Aug  3 14:04:34 2017
##################################################

from gnuradio import analog
from gnuradio import audio
from gnuradio import blocks
from gnuradio import eng_notation
from gnuradio import filter
from gnuradio import gr
from gnuradio.eng_option import eng_option
from gnuradio.filter import firdes
from gnuradio.filter import pfb

import argparse
import trollius as asyncio
import exceptions
import osmosdr
import re
import subprocess
import sip
import sys

# Command to run multimon-ng
# Can't avoid using sox (for unknown reasons multimon-ng doesn't like direct output from GNURadio) so convert from our channel rate to what it wants
cmdpat = "sox -t raw -esigned-integer -b16 -r {audio_in} - -esigned-integer -b16 -r {audio_out} -t raw - | multimon-ng -t raw -q -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -a FLEX -e -u -"

pocsagre = re.compile('([A-Z0-9]+): Address: +([0-9]+) +Function: +([0-9]+) +([A-Za-z]+): (.*)')
flexre = re.compile('FLEX: ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}) ([0-9]+)/([0-9]+)/([A-Za-z]) ([0-9]+).([0-9]+) \[([0-9]+)\] ALN (.*)')
def parse_multimon(fh, chfreq):
    line = fh.readline().strip()
    if print_pocsag(chfreq, line):
        return
    elif print_flex(chfreq, line):
        return
    else:
        print('Unparseable line "%s"' % (line))

def print_pocsag(chfreq, line):
    m = pocsagre.match(line)
    if m == None:
        return False
    (rate, address, function, ptype, msg) = m.groups()
    print('%.4f Mhz: %s: Address %s Function: %s %s: %s' % (chfreq / 1e6, rate, address, function, ptype, msg))
    return True

def print_flex(chfreq, line):
    m = flexre.match(line)
    if m == None:
        return False
    (ts, baud, level, phaseno, cycleno, frameno, capcode, msg) = m.groups()
    print('%.4f MHz: FLEX %s %s/%s/%s %s.%s [%s] ALN: %s' % (chfreq / 1e6, ts, baud, level, phaseno, cycleno, frameno, capcode, msg))

    return True

class MultiPager(gr.top_block):
    def __init__(self, freq, ch_width, num_chan, audio_rate, squelch, out_scale, loop,
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
            self.source.set_sample_rate(sample_rate)
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
            print('Resampling %f' % (file_samprate / sample_rate))
            self.filter = filter.fir_filter_ccf(1, firdes.low_pass(
                1, file_samprate, sample_rate / 2, 1e4, firdes.WIN_HAMMING, 6.76))
            self.resampler = filter.fractional_resampler_cc(0, file_samprate / sample_rate)
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
                print("Channel %d %.3f MHz" % (i, chfreq / 1e6))
                command = cmdpat.format(audio_in = ch_width, audio_out = audio_rate)
                fm = FMtoCommand(squelch, int(ch_width), 5e3, out_scale, chfreq, command, do_audio = (i == 0) and False)

                self.connect((self.pfb_channelizer_ccf_0, i), (fm, 0))
                self.fms[chfreq] = fm
                loop.add_reader(fm.p.stdout, parse_multimon, fm.p.stdout, chfreq)
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
        self.p = subprocess.Popen(command, shell = True, stdin = subprocess.PIPE, stdout = subprocess.PIPE)
        self.sink = blocks.file_descriptor_sink(gr.sizeof_short*1, self.p.stdin.fileno())
        self.connect(self, (self.analog_pwr_squelch, 0))
        self.connect((self.analog_pwr_squelch, 0), (self.analog_nbfm_rx, 0))
        self.connect((self.analog_nbfm_rx, 0), (self.blocks_float_to_short, 0))
        self.connect((self.blocks_float_to_short, 0), (self.sink, 0))
        if do_audio:
            self.resampler = filter.rational_resampler_fff(
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
    parser = argparse.ArgumentParser()
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

    args = parser.parse_args()
    if not (args.args == None) ^ (args.samplefile == None):
        parser.error('Must have Osmocom SDR arguments or samplefile')

    if args.samplefile != None and args.filerate == None:
        parser.error('Must specify sample rate when using file')

    ch_width = 25e3
    audio_rate = 22.05e3

    loop = asyncio.get_event_loop()

    tb = MultiPager(args.frequency,
                        ch_width,
                        args.channels,
                        audio_rate,
                        args.squelch,
                        args.outscale,
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
