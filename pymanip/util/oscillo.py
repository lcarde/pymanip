"""

Implements a live acquisition for NI cards

"""

import sys
import signal
import asyncio
import time
import math
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, TextBox, CheckButtons, RadioButtons
import h5py

from nidaqmx import Task
from nidaqmx.constants import READ_ALL_AVAILABLE
from nidaqmx.errors import DaqError
from nidaqmx.constants import TerminalConfiguration
from nidaqmx.system import system, device

class Oscillo:

    def __init__(self, channel_list, sampling, volt_range, trigger_level=None,
                 trigsource=0):
        self.channel_list = channel_list
        self.sampling = sampling
        self.volt_range = volt_range
        self.ignore_voltrange_submit = False
        self.trigger_level = trigger_level
        self.trigger_source = trigsource
        self.N = 1024
        plt.ion()
        self.fig = plt.figure()
        # left, bottom, width, height
        self.ax = self.fig.add_axes([0.1,0.1,0.7,0.8])
        self.running = False
        self.last_trigged = 0
        self.ask_pause_acqui = False
        self.paused = False
        
        self.freq = None
        self.Pxx = None
        self.N_spectra = 0
        self.fig_spectrum = None
        self.hanning = True
        self.ac = True
        self.power_spectrum = True
        self.spectrum_unit = 1.0
        self.spectrum_unit_str = 'V^2/Hz'
        self.task = None
        self.saved_spectra = list()
        
        self.fig_stats = None

        # Configure widgets
        ax_sampling = self.fig.add_axes([0.825, 0.825, 0.15, 0.075])
        self.textbox_sampling = TextBox(ax_sampling, label='',
                                        initial=f'{sampling:.1e}')
        self.textbox_sampling.on_submit(self.ask_sampling_change)
        label_sampling = ax_sampling.text(0, 1.25, 'Sampling')

        ax_enable_trigger = self.fig.add_axes([0.825, 0.72, 0.15, 0.075])
        self.checkbox_trigger = CheckButtons(ax_enable_trigger, ['Trigger'], [trigger_level is not None])
        self.checkbox_trigger.on_clicked(self.ask_trigger_change)

        ax_triggerlevel = self.fig.add_axes([0.825, 0.58, 0.15, 0.075])
        self.textbox_triggerlevel = TextBox(ax_triggerlevel, label='',
                                            initial=f'{trigger_level:.2f}' if trigger_level is not None else '1.0')
        self.textbox_triggerlevel.on_submit(self.ask_trigger_change)
        label_triggerlevel = ax_triggerlevel.text(0, 1.25, 'Level')

        ax_winsize = self.fig.add_axes([0.825, 0.44, 0.15, 0.075])
        self.textbox_winsize = TextBox(ax_winsize, label='',
                                       initial=f'{self.N:d}')
        self.textbox_winsize.on_submit(self.ask_winsize_change)
        label_winsize = ax_winsize.text(0, 1.25, 'Win size')

        ax_voltrange = self.fig.add_axes([0.825, 0.31, 0.15, 0.075])
        self.textbox_voltrange = TextBox(ax_voltrange, label='',
                                         initial=f'{self.volt_range:.1f}')
        self.textbox_voltrange.on_submit(self.ask_voltrange_change)
        label_voltrange = ax_voltrange.text(0, 1.25, 'Range')

        ax_start_stats = self.fig.add_axes([0.825, 0.2, 0.15, 0.075])
        self.btn_start_stats = Button(ax_start_stats, label='Stats')
        self.btn_start_stats.on_clicked(self.start_stats)
        
        ax_start_spectrum = self.fig.add_axes([0.825, 0.08, 0.15, 0.075])
        self.btn_start_spectrum = Button(ax_start_spectrum, label='FFT')
        self.btn_start_spectrum.on_clicked(self.start_spectrum)
        
    def clean_spectrum(self, *args):
        self.freq = None
        self.Pxx = None
        self.N_spectra = 0
    
    def start_stats(self, event):
        if self.fig_stats is None:
            self.fig_stats = dict()
            self.box_mean = dict()
            self.box_std = dict()
            self.box_min = dict()
            self.box_max = dict()
            self.box_freq = dict()
        nbox = 5
        height = 1.0/(nbox+1)
        padding = height/4
        for chan in self.channel_list:
            if chan not in self.fig_stats:
                self.fig_stats[chan] = plt.figure(figsize=(2,4))
                
                ax_mean = self.fig_stats[chan].add_axes([0.25, 9*height/2, 0.7, height-padding])
                self.box_mean[chan] = TextBox(ax_mean, label='Mean', initial='')
                
                ax_std = self.fig_stats[chan].add_axes([0.25, 7*height/2, 0.7, height-padding])
                self.box_std[chan] = TextBox(ax_std, label='Std', initial='')
                
                ax_min = self.fig_stats[chan].add_axes([0.25, 5*height/2, 0.7, height-padding])
                self.box_min[chan] = TextBox(ax_min, label='Min', initial='')
                
                ax_max = self.fig_stats[chan].add_axes([0.25, 3*height/2, 0.7, height-padding])
                self.box_max[chan] = TextBox(ax_max, label='Max', initial='')
                
                ax_freq = self.fig_stats[chan].add_axes([0.25, height/2, 0.7, height-padding])
                self.box_freq[chan] = TextBox(ax_freq, label='Freq', initial='')
                
    def start_spectrum(self, *args, **kwargs):
        if self.fig_spectrum is None:
            self.fig_spectrum = plt.figure()
            self.ax_spectrum = self.fig_spectrum.add_axes([0.1,0.1,0.7,0.8])

            # Widgets
            ax_hanning = self.fig_spectrum.add_axes([0.825, 0.75, 0.15, 0.15])
            self.checkbox_hanning = CheckButtons(ax_hanning, ['Hanning', 'AC'], [self.hanning, self.ac])
            self.checkbox_hanning.on_clicked(self.ask_hanning_change)

            ax_spectrum_unit = self.fig_spectrum.add_axes([0.825, 0.51, 0.15, 0.25])
            self.radio_units = RadioButtons(ax_spectrum_unit, ['V^2/Hz', 
                                                               'V/sq(Hz)',
                                                               'mV/sq(Hz)',
                                                               'µV/sq(Hz)',
                                                               'nV/sq(Hz)'])
            self.radio_units.on_clicked(self.ask_spectrum_units_change)

            ax_restart = self.fig_spectrum.add_axes([0.825, 0.35, 0.15, 0.075])
            self.btn_restart = Button(ax_restart, label='Restart')
            self.btn_restart.on_clicked(self.clean_spectrum)
            
            ax_save_hold = self.fig_spectrum.add_axes([0.825, 0.25, 0.15, 0.075])
            self.btn_save_hold = Button(ax_save_hold, label='Hold&Save')
            self.btn_save_hold.on_clicked(self.save_hold)

        self.clean_spectrum()

    def save_hold(self, event):
        if self.N_spectra > 0:
            dt = datetime.now()
            filename = f'pymanip_oscillo_{dt.year:}-{dt.month}-{dt.day}_{dt.hour}-{dt.minute}-{dt.second}.hdf5'
            bb = self.freq > 0
            with h5py.File(filename) as f:
                f.attrs['ts'] = dt.timestamp()
                f.attrs['N_spectra'] = self.N_spectra
                f.attrs['sampling'] = self.sampling
                f.attrs['volt_range'] = self.volt_range
                f.attrs['N'] = self.N
                f.create_dataset('freq', data=self.freq[bb])
                f.create_dataset('Pxx', data=self.Pxx[bb]/self.N_spectra)
            self.saved_spectra.append({'freq': self.freq[bb],
                                       'Pxx': self.Pxx[bb]/self.N_spectra})
            
        
    def ask_spectrum_units_change(self, event):
        power_spectrum_dict = {'V^2/Hz': True, 
                               'V/sq(Hz)': False,
                               'mV/sq(Hz)': False,
                               'µV/sq(Hz)': False,
                               'nV/sq(Hz)': False}
        spectrum_unit_dict = {'V^2/Hz': 1.0, 
                               'V/sq(Hz)': 1.0,
                               'mV/sq(Hz)': 1e3,
                               'µV/sq(Hz)': 1e6,
                               'nV/sq(Hz)': 1e9}
        if event in power_spectrum_dict:
            self.spectrum_unit_str = event
            self.power_spectrum = power_spectrum_dict[event]
            self.spectrum_unit = spectrum_unit_dict[event]

    async def winsize_change(self, new_N):
        await self.pause_acqui()
        old_N = self.N
        self.N = new_N
        self.clean_spectrum()
        self.figure_t_axis()
        try:
            self.task.timing.cfg_samp_clk_timing(self.sampling, samps_per_chan=self.N)
        except DaqError:
            print('Invalid winsize')
            self.N = old_N
        await self.restart_acqui()

    def ask_winsize_change(self, label):
        try:
            new_N = int(label)
        except ValueError:
            print('winsize must be an integer')
            return
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(self.winsize_change(new_N), loop=loop)

    def ask_trigger_change(self, label):
        changed = False
        trigger_enable, = self.checkbox_trigger.get_status()
        if trigger_enable:
            if self.trigger_level is None:
                changed = True
            try:
                self.trigger_level = float(self.textbox_triggerlevel.text)
                changed = True
            except ValueError:
                if self.trigger_level is not None:
                    self.textbox_triggerlevel.set_val(f'{self.trigger_level:.2f}')
                else:
                    self.textbox_triggerlevel.set_val('1.0')
                    changed = True
        else:
            if self.trigger_level is not None:
                changed = True
            self.trigger_level = None
        if changed:
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(self.trigger_change(), loop=loop)

    async def pause_acqui(self):
        self.ask_pause_acqui = True
        while not self.paused:
            await asyncio.sleep(0.5)

    async def restart_acqui(self):
        self.ask_pause_acqui = False
        while self.paused:
            await asyncio.sleep(0.5)

    async def trigger_change(self):
        await self.pause_acqui()
        if self.trigger_level is not None:
            self.task.triggers.start_trigger.cfg_anlg_edge_start_trig(self.channel_list[self.trigger_source],
                                                                      trigger_level=self.trigger_level)
        else:
            self.task.triggers.start_trigger.disable_start_trig()
        self.clean_spectrum()
        await self.restart_acqui()

    async def sampling_change(self):
        await self.pause_acqui()
        try:
            self.task.timing.cfg_samp_clk_timing(self.sampling, samps_per_chan=self.N)
        except DaqError:
            print('Invalid sampling frequency')
            self.ask_sampling_change(self.task.timing.samp_clk_max_rate)
            return
        self.figure_t_axis()
        self.clean_spectrum()
        await self.restart_acqui()
        
    def ask_sampling_change(self, sampling):
        try:
            self.sampling = float(sampling)
            changed = True
        except ValueError:
            print('Wrong value:', sampling)
            changed = False
        self.textbox_sampling.set_val(f'{self.sampling:.1e}')
        if changed:
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(self.sampling_change(), loop=loop)

    def ask_hanning_change(self, label):
        self.hanning, self.ac = self.checkbox_hanning.get_status()
        self.clean_spectrum()

    def figure_t_axis(self):
        self.t = np.arange(self.N)/self.sampling
        if self.t[-1] < 1:
            self.t *= 1000
            self.unit = '[ms]'
        else:
            self.unit = '[s]'

    async def run_gui(self):
        while self.running:
            if time.monotonic()-self.last_trigged > self.N/self.sampling:
                self.ax.set_title('Waiting for trigger')
            self.fig.canvas.start_event_loop(0.5)
            await asyncio.sleep(0.05)
            if not plt.fignum_exists(self.fig.number):
                self.running = False
            if self.fig_spectrum and not plt.fignum_exists(self.fig_spectrum.number):
                self.fig_spectrum = None
                self.freq = None
                self.Pxx = None
                self.N_spectra = 0
            if self.fig_stats is not None:
                for chan in list(self.fig_stats.keys()):
                    if not plt.fignum_exists(self.fig_stats[chan].number):
                        self.fig_stats.pop(chan)
                        self.box_mean.pop(chan)
                        self.box_std.pop(chan)
                        self.box_min.pop(chan)
                        self.box_max.pop(chan)
                        self.box_freq.pop(chan)
                    if not self.fig_stats:
                        self.fig_stats = None

    def ask_voltrange_change(self, new_range):
        if not self.ignore_voltrange_submit:
            try:
                new_range = float(new_range)
            except ValueError:
                print('Volt range must be a float')
                return
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(self.voltrange_change(new_range), loop=loop)

    async def voltrange_change(self, new_range):
        await self.pause_acqui()
        self.volt_range = new_range
        self.clean_spectrum()
        self.create_task()
        await self.restart_acqui()
        actual_range = self.ai_channels[0].ai_max
        print('actual_range =', actual_range)
        self.ignore_voltrange_submit = True
        self.textbox_voltrange.set_val(f'{actual_range:.1f}')
        self.ignore_voltrange_submit = False

    def create_task(self):
        if self.task is not None:
            self.task.close()
        self.task = Task()
        self.ai_channels = list()
        for chan in self.channel_list:
            ai_chan = self.task.ai_channels.add_ai_voltage_chan(chan,
                                                      terminal_config=TerminalConfiguration.DIFFERENTIAL,
                                                      min_val=-self.volt_range, 
                                                      max_val=self.volt_range)
            self.ai_channels.append(ai_chan)
        self.task.timing.cfg_samp_clk_timing(self.sampling, samps_per_chan=self.N)
        if self.trigger_level is not None:
            self.task.triggers.start_trigger.cfg_anlg_edge_start_trig(self.channel_list[self.trigger_source], 
                                                                      trigger_level=self.trigger_level)
        self.figure_t_axis()

    async def run_acqui(self):
        loop = asyncio.get_event_loop()
        self.create_task()
        try:
            while self.running:
                while self.ask_pause_acqui and self.running:
                    self.paused = True
                    await asyncio.sleep(0.5)
                #if self.paused:
                #    print('Exiting pause loop')
                #    print('self.t.shape =', self.t.shape)
                self.paused = False
                if not self.running:
                    break
                self.task.start()
                done = False
                while not self.ask_pause_acqui and self.running:
                    try:
                        await loop.run_in_executor(None,
                                                   self.task.wait_until_done,
                                                   1.0)
                    except DaqError:
                        continue
                    done = True
                    break
                if done:
                    data = np.array(self.task.read(READ_ALL_AVAILABLE))
                    self.last_trigged = time.monotonic()
                self.task.stop()
                if not done:
                    continue
                self.ax.cla()
                if len(self.channel_list) == 1:
                    self.ax.plot(self.t, data, '-')
                elif len(self.channel_list) > 1:
                    for d in data:
                        self.ax.plot(self.t, d, '-')
                if self.trigger_level is not None:
                    self.ax.plot([self.t[0], self.t[-1]], [self.trigger_level]*2, 'g--')
                self.ax.set_xlim([self.t[0], self.t[-1]])
                self.ax.set_title('Trigged!')
                self.ax.set_xlabel('t ' + self.unit)
                if self.fig_spectrum:
                    self.ax_spectrum.cla()
                    if self.saved_spectra:
                        for spectra in self.saved_spectra:
                            self.ax_spectrum.loglog(spectra['freq'], spectra['Pxx'], '-')
                    if self.N_spectra == 0:
                        self.freq = np.fft.fftfreq(self.N, 1.0/self.sampling)
                        bb = self.freq > 0
                        norm = math.pi*math.sqrt(self.N/self.sampling) 
                        if self.hanning:
                            window = np.hanning(self.N)
                        else:
                            window = np.ones((self.N,))
                        if len(self.channel_list) == 1:
                            if self.ac:
                                m = np.mean(data)
                            else:
                                m = 0.0
                            self.Pxx = np.abs(np.fft.fft((data-m)*window)/norm)**2
                        else:
                            if self.ac:
                                ms = [np.mean(d) for d in data]
                            else:
                                ms = [0.0 for d in data]
                            self.Pxx = [np.abs(np.fft.fft((d-m)*window)/norm)**2 for d, m in zip(data, ms)]
                        self.N_spectra = 1
                    else:
                        if len(self.channel_list) == 1:
                            if self.ac:
                                m = np.mean(data)
                            else:
                                m = 0.0
                            self.Pxx += np.abs(np.fft.fft((data-m)*window)/norm)**2
                        else:
                            if self.ac:
                                ms = [np.mean(d) for d in data]
                            else:
                                ms = [0.0 for d in data]
                            for p, d, m in zip(self.Pxx, data, ms):
                                p += np.abs(np.fft.fft((d-m)*window)/norm)**2
                        self.N_spectra += 1
                    if self.power_spectrum:
                        def process_spec(s):
                            return self.spectrum_unit*s
                    else:
                        def process_spec(s):
                            return self.spectrum_unit*np.sqrt(s)
                    if len(self.channel_list) == 1:
                        self.ax_spectrum.loglog(self.freq[bb], process_spec(self.Pxx[bb]/self.N_spectra), '-')
                    else:
                        for p in self.Pxx:
                            self.ax_spectrum.loglog(self.freq[bb], process_spec(p[bb]/self.N_spectra), '-')
                    self.ax_spectrum.set_xlabel('f [Hz]')
                    self.ax_spectrum.set_ylabel(self.spectrum_unit_str)
                    self.ax_spectrum.set_title(f'N = {self.N_spectra:d}')
                if self.fig_stats:
                    if len(self.channel_list) == 1:
                        list_data = [data]
                    else:
                        list_data = data
                    for chan, d in zip(self.channel_list, list_data):
                        if chan in self.fig_stats:
                            self.box_mean[chan].set_val('{:.5f}'.format(np.mean(d)))
                            self.box_std[chan].set_val('{:.5f}'.format(np.std(d)))
                            self.box_min[chan].set_val('{:.5f}'.format(np.min(d)))
                            self.box_max[chan].set_val('{:.5f}'.format(np.max(d)))
                            ff = np.fft.fftfreq(self.N, 1.0/self.sampling)
                            pp = np.abs(np.fft.fft(d-np.mean(d)))
                            ii = np.argmax(pp)
                            self.box_freq[chan].set_val('{:.5f}'.format(ff[ii]))
                        
        finally:
            self.task.close()

    def ask_exit(self, *args, **kwargs):
        self.running = False

    def run(self):
        loop = asyncio.get_event_loop()
        self.running = True
        if sys.platform == 'win32':
            signal.signal(signal.SIGINT, self.ask_exit)
        else:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.add_signal_handler(getattr(signal, signame),
                                        self.ask_exit)

        loop.run_until_complete(asyncio.gather(self.run_gui(),
                                               self.run_acqui()))


class ChannelSelector:

    def __init__(self):
        self.sys = system.System()
        self.device_list = dict()
        for devname in self.sys.devices.device_names:
            dev = device.Device(devname)
            description = dev.product_type
            if description.startswith('PXI'):
                description = f'PXI {dev.pxi_chassis_num:d} Slot {dev.pxi_slot_num:d} ({dev.product_type:})'
            elif description.startswith('PCI'):
                description = f'{dev.product_type:} ({dev.pci_bus_num:} {dev.pci_dev_num:})'
            self.device_list[description] = dev.ai_physical_chans.channel_names
            
    def print_channel_list(self):
        for name, devlist in self.device_list.items():
            print(name)
            print('-'*len(name))
            print(devlist)


if __name__ == '__main__':
    chansel = ChannelSelector()
    chansel.print_channel_list()
    #oscillo = Oscillo(['Dev2/ai0', 'Dev2/ai1'], 5e3, 10.0, trigger_level=2.0)
    #oscillo.run()
