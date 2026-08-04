#!/usr/bin/env python3
import re
import os
import time
import json
import sys
import subprocess
import multiprocessing as mp
import traceback

import gpiod
from configparser import ConfigParser
from collections import defaultdict, OrderedDict

cmds = {
    'blk': "lsblk | awk '{print $1}'",
    'up': "echo Up: $(uptime -p | sed 's/ years,/y/g;s/ year,/y/g;s/ months,/m/g;s/ month,/m/g;s/ weeks,/w/g;s/ week,/w/g;s/ days,/d/g;s/ day,/d/g;s/ hours,/h/g;s/ hour,/h/g;s/ minutes/m/g;s/ minute/m/g' | cut -d ' ' -f2-)",    'temp': "cat /sys/class/thermal/thermal_zone0/temp",
    'temp': "cat /sys/class/thermal/thermal_zone0/temp",
    'ip': "hostname -I | awk '{printf \"IP %s\", $1}'",
    'cpu': "uptime | awk '{printf \"CPU Load: %.2f\", $(NF-2)}'",
    'men': "free -m | awk 'NR==2{printf \"Mem: %s/%sMB\", $3,$2}'",
    'disk': "df -h | awk '$NF==\"/\"{printf \"Disk: %d/%dGB %s\", $3,$2,$5}'"
}

lv2dc = OrderedDict({'lv3': 100, 'lv2': 75, 'lv1': 50, 'lv0': 25})
sata_lines = []
GPIOD_V2 = hasattr(gpiod, 'LineSettings')

# we hold raw data for MB count and second of sample time
raw_interface_io = defaultdict(dict)
raw_disk_io = defaultdict(dict)

# we hold the calculated transfer rates in MB/s
interface_io_rate = defaultdict(dict)
disk_io_rate = defaultdict(dict)

# we hold the drive sector size since linux reports in sectors transferred
disk_sector_sizes = defaultdict(dict)

def gpiochip_path(chip):
    chip = str(chip)
    if chip.startswith('/dev/'):
        return chip
    if chip.startswith('gpiochip'):
        return f'/dev/{chip}'
    return f'/dev/gpiochip{chip}'


def request_input_line(chip_name, line_number, consumer, pull_up=False):
    line_number = int(line_number)
    if GPIOD_V2:
        settings = {
            'direction': gpiod.line.Direction.INPUT,
        }
        if pull_up:
            settings['bias'] = gpiod.line.Bias.PULL_UP
        chip = gpiod.Chip(gpiochip_path(chip_name))
        request = chip.request_lines(
            config={line_number: gpiod.LineSettings(**settings)},
            consumer=consumer,
        )
        return chip, request

    chip = gpiod.Chip(str(chip_name))
    line = chip.get_line(line_number)
    request = {
        'consumer': consumer,
        'type': gpiod.LINE_REQ_DIR_IN,
    }
    if pull_up and hasattr(gpiod, 'LINE_REQ_FLAG_BIAS_PULL_UP'):
        request['flags'] = gpiod.LINE_REQ_FLAG_BIAS_PULL_UP
    line.request(**request)
    return chip, line


def read_line(handle, line_number):
    line_number = int(line_number)
    if GPIOD_V2:
        return int(handle.get_value(line_number) == gpiod.line.Value.ACTIVE)
    return handle.get_value()


def release_line(chip, handle):
    try:
        handle.release()
    finally:
        if hasattr(chip, 'close'):
            chip.close()


def check_output(cmd):
    return subprocess.check_output(cmd, shell=True).decode().strip()


def check_call(cmd):
    return subprocess.check_call(cmd, shell=True)


def get_blk():
    conf['disk'] = [x for x in check_output(cmds['blk']).strip().split('\n') if x.startswith('sd')]


def get_info(s):
    return check_output(cmds[s])


def get_cpu_temp():
    t = float(get_info('temp')) / 1000
    if conf['oled']['f-temp']:
        temp = "CPU Temp: {:.0f}°F".format(t * 1.8 + 32)
    else:
        temp = "CPU Temp: {:.1f}°C".format(t)
    return temp


def read_conf():
    conf = defaultdict(dict)

    try:
        cfg = ConfigParser()
        cfg.read('/etc/rockpi-quad.conf')
        # fan
        conf['fan']['lv0'] = cfg.getfloat('fan', 'lv0')
        conf['fan']['lv1'] = cfg.getfloat('fan', 'lv1')
        conf['fan']['lv2'] = cfg.getfloat('fan', 'lv2')
        conf['fan']['lv3'] = cfg.getfloat('fan', 'lv3')
        # key
        conf['key']['click'] = cfg.get('key', 'click')
        conf['key']['twice'] = cfg.get('key', 'twice')
        conf['key']['press'] = cfg.get('key', 'press')
        # time
        conf['time']['twice'] = cfg.getfloat('time', 'twice')
        conf['time']['press'] = cfg.getfloat('time', 'press')
        # other
        conf['slider']['auto'] = cfg.getboolean('slider', 'auto')
        conf['slider']['time'] = cfg.getfloat('slider', 'time')
        conf['oled']['rotate'] = cfg.getboolean('oled', 'rotate')
        conf['oled']['f-temp'] = cfg.getboolean('oled', 'f-temp')
    except Exception:
        traceback.print_exc()
        # fan
        conf['fan']['lv0'] = 35
        conf['fan']['lv1'] = 40
        conf['fan']['lv2'] = 45
        conf['fan']['lv3'] = 50
        # key
        conf['key']['click'] = 'slider'
        conf['key']['twice'] = 'switch'
        conf['key']['press'] = 'none'
        # time
        conf['time']['twice'] = 0.7  # second
        conf['time']['press'] = 1.8
        # other
        conf['slider']['auto'] = True
        conf['slider']['time'] = 10  # second
        conf['oled']['rotate'] = False
        conf['oled']['f-temp'] = False

    return conf


def read_key(pattern, size):
    CHIP_NAME = os.environ['BUTTON_CHIP']
    LINE_NUMBER = int(os.environ['BUTTON_LINE'])

    s = ''
    chip, line = request_input_line(CHIP_NAME, LINE_NUMBER, 'hat_button', pull_up=True)

    try:
        while True:
            s = s[-size:] + str(read_line(line, LINE_NUMBER))
            for t, p in pattern.items():
                if p.match(s):
                    return t
            time.sleep(0.1)
    finally:
        release_line(chip, line)


def watch_key(q=None):
    size = int(conf['time']['press'] * 10)
    wait = int(conf['time']['twice'] * 10)
    pattern = {
        'click': re.compile(r'1+0+1{%d,}' % wait),
        'twice': re.compile(r'1+0+1+0+1{3,}'),
        'press': re.compile(r'1+0{%d,}' % size),
    }

    while True:
        q.put(read_key(pattern, size))

def get_interface_list():
    if len(conf['network']['interfaces']) == 1 and conf['network']['interfaces'][0] == '':
        return []

    if len(conf['network']['interfaces']) == 1 and conf['network']['interfaces'][0] == 'auto':
        interfaces = []
        cmd = "ip -o link show | awk '{print $2,$9}'"
        list = check_output(cmd).split('\n')
        for x in list:
            name_status = x.split(': ')
            if "UP" in name_status[1]:
                interfaces.append(name_status[0])

        interfaces.sort()

    else:
        interfaces = conf['network']['interfaces']

    return interfaces


def get_interface_rx_info(interface):
    cmd = "R1=$(cat /sys/class/net/" + interface + "/statistics/rx_bytes); sleep 1; R2=$(cat /sys/class/net/" + interface + "/statistics/rx_bytes); echo | awk -v r1=$R1 -v r2=$R2 '{printf \"rx: %.5f MB/s\", (r2 - r1) / 1024 / 1024}';"
    output = check_output(cmd)
    return output


def get_interface_tx_info(interface):
    cmd = "T1=$(cat /sys/class/net/" + interface + "/statistics/tx_bytes); sleep 1; T2=$(cat /sys/class/net/" + interface + "/statistics/tx_bytes); echo | awk -v t1=$T1 -v t2=$T2 '{printf \"tx: %.5f MB/s\", (t2 - t1) / 1024 / 1024}';"
    output = check_output(cmd)
    return output


def delete_disk_partition_number(disk):
    if "sd" in disk and disk[-1].isdigit():
        disk = disk[:-1]
    return disk


def get_disk_list(type):
    if len(conf['disk'][type]) == 1 and conf['disk'][type][0] == '':
        return []

    disks = []
    for x in conf['disk'][type]:
        cmd = "df -Bg | awk '$6==\"{}\" {{printf \"%s\", $1}}'".format(x)
        output = check_output(cmd).split('/')[-1]
        if output != '':
            disks.append(output)

    disks.sort()
    return disks


def get_disk_temp_info():
    if not conf['disk']['disks_temp']:
        return [(), ()]
    disks = list(check_output("ls /dev/sd* | grep -E \"[0-9]$\" | cut -f3 -d'/' | tr -d '0123456789'").split("\n"))
    disks_temp = {}
    for disk in disks:
        output = check_output(f"sudo smartctl -A /dev/{disk} -j")
        object = json.loads(output)
        disk_temp = object["temperature"]["current"]
        if conf['oled']['f-temp']:
            disk_temp = "{:.0f}°F".format(disk_temp * 1.8 + 32)
        else:
            disk_temp = "{}°C".format(disk_temp)
        disks_temp[disk] = disk_temp
    return list(zip(*disks_temp.items()))


def get_disk_io_read_info(disk):
    cmd = "R1=$(cat /sys/block/" + disk + "/stat | awk '{print $3}'); sleep 1; R2=$(cat /sys/block/" + disk + "/stat | awk '{print $3}'); echo | awk -v r1=$R1 -v r2=$R2 '{printf \"R: %.5f MB/s\", (r2 - r1) / 2 / 1024}';"
    output = check_output(cmd)
    return output


def get_disk_io_write_info(disk):
    cmd = "W1=$(cat /sys/block/" + disk + "/stat | awk '{print $7}'); sleep 1; W2=$(cat /sys/block/" + disk + "/stat | awk '{print $7}'); echo | awk -v w1=$W1 -v w2=$W2 '{printf \"W: %.5f MB/s\", (w2 - w1) / 2 / 1024}';"
    output = check_output(cmd)
    return output


def get_disk_info(cache={}):
    if not cache.get('time') or time.time() - cache['time'] > 30:
        info = {}
        cmd = "df -h | awk '$NF==\"/\"{printf \"%s\", $5}'"
        info['root'] = check_output(cmd)
        conf['disk']['disks'] = get_disk_list('space_usage_mnt_points')
        for x in conf['disk']['disks']:
            delete_disk_partition_number(x)
        cache['info'] = list(zip(*info.items()))
        cache['time'] = time.time()

    return cache['info']

def get_sector_size(disk):
    cmd = "cat /sys/block/" + disk + "/queue/hw_sector_size"
    disk_sector_sizes[disk] = int(check_output(cmd))

def slider_next(pages):
    conf['idx'].value += 1
    return pages[conf['idx'].value % len(pages)]


def slider_sleep():
    time.sleep(conf['slider']['time'])


def fan_temp2dc(temp):
    if conf['fan']['linear']:
        lv0_percent = lv2dc['lv0']
        lv3_percent = lv2dc['lv3']
        base_temp = conf['fan']['lv0']
        denominator = conf['fan']['lv3'] - base_temp
        slope = (lv3_percent - lv0_percent) / denominator if denominator > 0 else 1.0
        dc = min(lv3_percent, max(slope * (temp - base_temp) + lv0_percent, lv0_percent))
        return dc
    else:
        for lv, dc in lv2dc.items():
            if temp >= conf['fan'][lv]:
                return dc
    return 10


def fan_switch():
    conf['run'].value = not conf['run'].value

def fan_running():
    return conf['run'].value

def get_func(key):
    return conf['key'].get(key, 'none')


def disk_turn_on():
    global sata_lines
    chip_name = os.environ['SATA_CHIP']
    line1 = int(os.environ['SATA_LINE_1'])
    line2 = int(os.environ['SATA_LINE_2'])

    if GPIOD_V2:
        chip = gpiod.Chip(gpiochip_path(chip_name))
        settings = gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT)
        request = chip.request_lines(
            config={(line1, line2): settings},
            consumer='sata_power',
            output_values={
                line1: gpiod.line.Value.ACTIVE,
                line2: gpiod.line.Value.ACTIVE,
            },
        )
        sata_lines = [chip, request]
        return

    chip = gpiod.Chip(str(chip_name))
    line1 = chip.get_line(line1)
    line1.request(consumer='SATA_LINE_1', type=gpiod.LINE_REQ_DIR_OUT)
    line1.set_value(1)
    line2 = chip.get_line(line2)
    line2.request(consumer='SATA_LINE_2', type=gpiod.LINE_REQ_DIR_OUT)
    line2.set_value(1)
    sata_lines = [chip, line1, line2]


conf = {'disk': [], 'idx': mp.Value('d', -1), 'run': mp.Value('d', 1)}
conf.update(read_conf())
