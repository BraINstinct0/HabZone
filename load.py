# -*- coding: utf-8 -*-
#
# Display the "habitable-zone" (i.e. the range of distances in which you might find an Earth-Like World)
#

from __future__ import print_function

from collections import defaultdict
import requests
import sys
import threading
# Python 2 deprecated
from urllib.parse import quote
import tkinter as tk

from ttkHyperlinkLabel import HyperlinkLabel
import myNotebook as nb

if __debug__:
    from traceback import print_exc

from config import config
from l10n import Locale

VERSION = '1.20'

SETTING_DEFAULT = 0x0002	# Earth-like
SETTING_EDSM    = 0x1000
SETTING_NONE    = 0xffff

WORLDS = [
    # Type    Black-body temp range  EDSM description
    ('Metal-Rich',      0,  1103.0, 'Metal-rich body'),
    ('Earth-Like',    278.0, 227.0, 'Earth-like world'),
    ('Water',         307.0, 156.0, 'Water world'),
    ('Ammonia',       193.0, 117.0, 'Ammonia world'),
    ('Class II Giant', 250.0, 150.0, 'Class II gas giant'),
    ('Terraformable', 315.0, 223.0, 'terraformable'),
]
# Journal planet type to EDSM planet type
JRNL2TYPE = {
    'Earthlike body':'Earth-like world',
    'Water world':'Water world',
    'Ammonia world':'Ammonia world',
    'Metal rich body':'Metal-rich body',
    'Sudarsky class II gas giant':'Class II gas giant'
}

LS = 300000000.0	# 1 ls in m (approx)

this = sys.modules[__name__]	# For holding module globals
this.frame = None
this.worlds = []
this.scanned_worlds = {'system': None, 'bodies': {}}
this.edsm_session = None
this.edsm_data = None

# Used during preferences
this.settings = None
this.edsm_setting = None


def plugin_start3(plugin_dir):
    return plugin_start()

def plugin_start():
    # App isn't initialised at this point so can't do anything interesting
    return 'HabZone'

def plugin_app(parent):
    # Create and display widgets
    this.frame = tk.Frame(parent)
    this.frame.columnconfigure(3, weight=1)
    this.frame.bind('<<HabZoneData>>', edsm_data)	# callback when EDSM data received
    for (name, high, low, subType) in WORLDS:
        this.worlds.append((tk.Label(this.frame, text = name + ':'),
                            HyperlinkLabel(this.frame, wraplength=100),	# edsm
                            tk.Label(this.frame),	# near
                            tk.Label(this.frame),	# dash
                            tk.Label(this.frame),	# far
                            tk.Label(this.frame),	# ls
                            ))
    this.spacer = tk.Frame(this.frame)	# Main frame can't be empty or it doesn't resize
    update_visibility()
    return this.frame

def plugin_prefs(parent, cmdr, is_beta):
    frame = nb.Frame(parent)
    nb.Label(frame, text = 'Display:').grid(row = 0, padx = 10, pady = (10,0), sticky=tk.W)

    setting = get_setting()
    this.settings = []
    row = 1
    for (name, high, low, subType) in WORLDS:
        var = tk.IntVar(value = (setting & row) and 1)
        nb.Checkbutton(frame, text = name, variable = var).grid(row = row, padx = 10, pady = 2, sticky=tk.W)
        this.settings.append(var)
        row *= 2

    nb.Label(frame, text = 'Elite Dangerous Star Map:').grid(padx = 10, pady = (10,0), sticky=tk.W)
    this.edsm_setting = tk.IntVar(value = (setting & SETTING_EDSM) and 1)
    nb.Checkbutton(frame, text = 'Look up system in EDSM database', variable = this.edsm_setting).grid(padx = 10, pady = 2, sticky=tk.W)

    nb.Label(frame, text = 'Version %s' % VERSION).grid(padx = 10, pady = 10, sticky=tk.W)

    return frame

def prefs_changed(cmdr, is_beta):
    row = 1
    setting = 0
    for var in this.settings:
        setting += var.get() and row
        row *= 2

    setting += this.edsm_setting.get() and SETTING_EDSM
    config.set('habzone', setting or SETTING_NONE)
    this.settings = None
    this.edsm_setting = None
    update_visibility()


def journal_entry(cmdr, is_beta, system, station, entry, state):

    if entry['event'] == 'Scan':
        try:
            if not float(entry['DistanceFromArrivalLS']):	# Only calculate for arrival star
                r = float(entry['Radius'])
                t = float(entry['SurfaceTemperature'])
                for i in range(len(WORLDS)):
                    (name, high, low, subType) = WORLDS[i]
                    (label, edsm, near, dash, far, ls) = this.worlds[i]
                    far_dist = int(0.5 + dfort(r, t, low))
                    radius = int(0.5 + r / LS)
                    if far_dist <= radius:
                        near['text'] = ''
                        dash['text'] = u'×'
                        far['text'] = ''
                        ls['text'] = ''
                    else:
                        if not high:
                            near['text'] = Locale.stringFromNumber(radius)
                        else:
                            near['text'] = Locale.stringFromNumber(int(0.5 + dfort(r, t, high)))
                        dash['text'] = '-'
                        far['text'] = Locale.stringFromNumber(far_dist)
                        ls['text'] = 'ls'
            if entry.get('TerraformState', False) or (entry.get('PlanetClass', False)):
                mapped = entry.get('WasMapped')
                # TODO: Clean up repetitive code - perhaps integrate Journal types into WORLDS constant?
                body_type = None
                if entry.get('TerraformState') == 'Terraformable':
                    body_type = 'terraformable'
                else:
                    try:
                        body_type = JRNL2TYPE[entry.get('PlanetClass')]
                    except:
                        pass
                if body_type:
                    data = this.scanned_worlds['bodies'].get(entry.get('BodyName'), {})
                    data.update({'type': body_type, 'was_mapped': mapped})
                    this.scanned_worlds['bodies'][entry.get('BodyName')] = data
                list_bodies(system)
        except (RuntimeError, TypeError) as err:
            for (label, edsm, near, dash, far, ls) in this.worlds:
                near['text'] = ''
                dash['text'] = ''
                far['text'] = ''
                ls['text'] = '?'
                edsm['test'] = err

    elif entry['event'] == 'FSDJump':
        for (label, edsm, near, dash, far, ls) in this.worlds:
            edsm['text'] = ''
            edsm['url'] = ''
            near['text'] = ''
            dash['text'] = ''
            far['text'] = ''
            ls['text'] = ''
        this.scanned_worlds['system'] = entry['StarSystem']
        this.scanned_worlds['bodies'].clear()

    if entry['event'] == 'SAAScanComplete':
        for name in this.scanned_worlds['bodies']:
            print('Scan Name: ' + name + ' | ' + entry['BodyName'])
            if name == entry['BodyName']:
                this.scanned_worlds['bodies'][name].update({'mapped': True})
        list_bodies(system)

    if entry['event'] in ['Location', 'FSDJump'] and get_setting() & SETTING_EDSM:
        thread = threading.Thread(target = edsm_worker, name = 'EDSM worker', args = (entry['StarSystem'],))
        thread.daemon = True
        thread.start()


def cmdr_data(data, is_beta):
    # Manual Update
    if get_setting() & SETTING_EDSM and not data['commander']['docked']:
        thread = threading.Thread(target = edsm_worker, name = 'EDSM worker', args = (data['lastSystem']['name'],))
        thread.daemon = True
        thread.start()


# Distance for target black-body temperature
# From Jackie Silver's Hab-Zone Calculator https://forums.frontier.co.uk/showthread.php?p=5452081
def dfort(r, t, target):
    return (((r ** 2) * (t ** 4) / (4 * (target ** 4))) ** 0.5) / LS


def list_bodies(system):
    body_data = {}
    for name in this.scanned_worlds['bodies']:
        if this.scanned_worlds['bodies'][name].get('type', False):
            final_name = name
            if this.scanned_worlds['bodies'][name].get('was_mapped', False):
                final_name += u'⍻'
            elif this.scanned_worlds['bodies'][name].get('mapped', False):
                final_name += u'🗸'
            data = body_data.get(this.scanned_worlds['bodies'][name]['type'], [])
            data.append(final_name)
            body_data[this.scanned_worlds['bodies'][name]['type']] = data
    for i in range(len(WORLDS)):
        (name, high, low, subType) = WORLDS[i]
        (label, edsm, near, dash, far, ls) = this.worlds[i]
        edsm['text'] = ' '.join([x[len(system):].replace(' ', '') if x.startswith(system) else '' for x in
                                 sorted(body_data.get(subType, []))])


# EDSM lookup
def edsm_worker(systemName):

    if not this.edsm_session:
        this.edsm_session = requests.Session()

    try:
        r = this.edsm_session.get('https://www.edsm.net/api-system-v1/bodies?systemName=%s' % quote(systemName), timeout=10)
        r.raise_for_status()
        this.edsm_data = r.json() or {}	# Unknown system represented as empty list
    except:
        this.edsm_data = None

    # Tk is not thread-safe, so can't access widgets in this thread.
    # event_generate() is the only safe way to poke the main thread from this thread.
    this.frame.event_generate('<<HabZoneData>>', when='tail')


# EDSM data received
def edsm_data(event):

    if this.edsm_data is None:
        # error
        for (label, edsm, near, dash, far, ls) in this.worlds:
            edsm['text'] = '?'
            edsm['url'] = None
        return

    # Collate
    for body in this.edsm_data.get('bodies', []):
        data = this.scanned_worlds['bodies'].get(body['name'], {})
        data.update({'type': ('terraformable' if (body.get('terraformingState') == 'Candidate for terraforming') else body['subType'])})
        this.scanned_worlds['bodies'][body['name']] = data
    
    # Display
    systemName = this.edsm_data.get('name', '')
    url = 'https://www.edsm.net/show-system?systemName=%s&bodyName=ALL' % quote(systemName)
    for i in range(len(WORLDS)):
        (name, high, low, subType) = WORLDS[i]
        (label, edsm, near, dash, far, ls) = this.worlds[i]
        list_bodies(systemName)
        edsm['url'] = url
        #edsm['url'] = len(this.scanned_worlds[subType]) == 1 and 'https://www.edsm.net/show-system?systemName=%s&bodyName=%s' % (quote(systemName), quote(this.scanned_worlds[subType][0])) or url


def get_setting():
    setting = config.getint('habzone')
    if setting == 0:
        return SETTING_DEFAULT	# Default to Earth-Like
    elif setting == SETTING_NONE:
        return 0	# Explicitly set by the user to display nothing
    else:
        return setting

def update_visibility():
    setting = get_setting()
    row = 1
    for (label, edsm, near, dash, far, ls) in this.worlds:
        if setting & row:
            label.grid(row = row, column = 0, sticky=tk.W)
            edsm.grid(row = row, column = 1, sticky=tk.W, padx = (0,10))
            near.grid(row = row, column = 2, sticky=tk.E)
            dash.grid(row = row, column = 3, sticky=tk.E)
            far.grid(row = row, column = 4, sticky=tk.E)
            ls.grid(row = row, column = 5, sticky=tk.W)
        else:
            for elem in (label, edsm, near, dash, far, ls):
                elem.grid_remove()
                              
        row *= 2
    if setting:
        this.spacer.grid_remove()
    else:
        this.spacer.grid(row = 0)
