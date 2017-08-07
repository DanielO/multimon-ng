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

import exceptions
import osmosdr
import subprocess
import sip
import sys

# Command to run multimon-ng
# Can't avoid using sox (for unknown reasons multimon-ng doesn't like direct output from GNURadio) so convert from our channel rate to what it wants
cmdpat = "sox -t raw -esigned-integer -b16 -r {audio_in} - -esigned-integer -b16 -r {audio_out} -t raw - | multimon-ng -t raw -q -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -e -u -f alpha --timestamp --label \"{chfreq:.3f} MHz:\" -"

class MultiPager(gr.top_block):
    def __init__(self, freq, ch_width, num_chan, audio_rate, squelch, out_scale,
                     filename = None, file_samprate = None,
                     osmo_args = None, osmo_rf_gain = None, osmo_if_gain = None, osmo_bb_gain = None):
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
            self.source.set_freq_corr(0, 0)
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
                command = cmdpat.format(audio_in = ch_width, audio_out = audio_rate, chfreq = (chfreq / 1e6))
                fm = FMtoCommand(squelch, int(ch_width), 5e3, out_scale, chfreq, command, do_audio = (i == 0) and False)

                self.connect((self.pfb_channelizer_ccf_0, i), (fm, 0))
                self.fms[chfreq] = fm
            else:
                n = blocks.null_sink(gr.sizeof_gr_complex*1)
                self.connect((self.pfb_channelizer_ccf_0, i), (n, 0))

class FMtoCommand(gr.hier_block2):
    def __init__(self, squelch, ch_width, max_dev, out_scale, freq, command, do_audio = False):
        gr.hier_block2.__init__(self, "FMtoCommand",
                                    gr.io_signature(1, 1, gr.sizeof_gr_complex),
                                    gr.io_signature(0, 0, gr.sizeof_gr_complex))

        analog_pwr_squelch = analog.pwr_squelch_cc(squelch, 1e-4, 0, True)
        analog_nbfm_rx = analog.nbfm_rx(
        	audio_rate = ch_width,
            quad_rate = ch_width,
        	tau = 75e-6,
        	max_dev = max_dev,
          )
        blocks_float_to_short = blocks.float_to_short(1, out_scale)
        # OSX: if you get Resource Temporarily Unavailable you probably need to increase maxproc, eg
        # sudo launchctl limit maxproc 2000 3000
        self.p = subprocess.Popen(command, shell = True, stdin = subprocess.PIPE)
        sink = blocks.file_descriptor_sink(gr.sizeof_short*1, self.p.stdin.fileno())
        self.connect(self, (analog_pwr_squelch, 0))
        self.connect((analog_pwr_squelch, 0), (analog_nbfm_rx, 0))
        self.connect((analog_nbfm_rx, 0), (blocks_float_to_short, 0))
        self.connect((blocks_float_to_short, 0), (sink, 0))
        if do_audio:
            resampler = filter.rational_resampler_fff(
                interpolation = 441,
                decimation = 425,
                taps = None,
                fractional_bw = None)
            mult = blocks.multiply_const_vff((0.2, ))
            audio_sink = audio.sink(22050, '', True)
            self.connect((analog_nbfm_rx, 0), (resampler, 0))
            self.connect((resampler, 0), (mult, 0))
            self.connect((mult, 0), (audio_sink, 0))

def main():
    freq = 148.664e6
    ch_width = 25e3
    audio_rate = 22.05e3
    num_chan = 35
    squelch = -20
    out_scale = 10000

    # File must be complex samples
    samplefile = 'sampler-22050x40.raw'

    tb = MultiPager(freq, ch_width, num_chan, audio_rate, squelch, out_scale,
                        #filename = samplefile,
                        #file_samprate = 22.05e3 * 40,
                        osmo_args = "hackrf",
                        osmo_rf_gain = 0,
                        osmo_if_gain = 36,
                        osmo_bb_gain = 44,
                        )
    tb.start()
    try:
        raw_input('Press Enter to quit: ')
    except EOFError:
        pass

    tb.stop()
    tb.wait()

if __name__ == '__main__':
    main()
