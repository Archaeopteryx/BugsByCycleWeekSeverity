# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# This scripts generates a report of the number of bugs created by week and
# grouped by severity for a given version number which got fixed before the
# release of that version.
# It does not imply that the code regressed during that version number, only
# that it initially got reported when it was in either Nightly (central) or
# Beta stage. The issue can have affected also lower version numbers if it got
# missed before or the regressing code got added to repository containing the
# lower version number ("uplift").

import argparse
import csv
import datetime
from dateutil.relativedelta import relativedelta
import json
from libmozdata.bugzilla import Bugzilla
from logger import logger
import productdates
import pytz
import utils

PRODUCTS_TO_CHECK = [
    'Core',
    'DevTools',
    'Firefox',
    'Firefox Build System',
    'Firefox for Android',
    'Testing',
    'Toolkit',
    'WebExtensions',
]

# TODO: Drop deprecated severities once existing uses have been updated
#       https://bugzilla.mozilla.org/show_bug.cgi?id=1564608
SEVERITIES = {
              'blocker': 'blocker+critical+major',
              'critical': 'blocker+critical+major',
              'major': 'blocker+critical+major',
              'normal': 'normal',
              'minor': 'minor+trivial',
              'trivial': 'minor+trivial',
              'enhancement': 'enhancement',
             }

SEVERITIES_LIST = [
                   'enhancement',
                   'trivial',
                   'minor',
                   'normal',
                   'major',
                   'critical',
                   'blocker',
                  ]

SEVERITIES_GROUP_LIST = [
                         'enhancement',
                         'minor+trivial',
                         'normal',
                         'blocker+critical+major',
                        ]

WFMT = '{}-{:02d}'

# Bugzilla data can be loaded from file
bugzilla_data_loaded = None

# Bugzilla data can be saved froto file
bugzilla_data_to_save = {}


def add_bugzilla_data_to_save(node_path, data):
    # node_path is an array of strings representing the nodes in the JSON to
    # which the data shall be saved. The node has a child 'data' which holds the
    # data.
    node = bugzilla_data_to_save
    for path_step in node_path:
        if not path_step in node:
            node[path_step] = {'data': []}
        node = node[path_step]
    node['data'].append(data)


def get_weeks(start_date, end_date):
    res = []
    while start_date.strftime('%Y-%W') <= end_date.strftime('%Y-%W'):
        y, w, _ = start_date.isocalendar()
        res.append(WFMT.format(y, w))
        start_date += relativedelta(days=7)
    return res


def get_bugs(major):

    def bug_handler(bug_data, other_data):
        data_opened = other_data['data_opened']
        phase = other_data['phase']

        if bzdata_save_path:
            add_bugzilla_data_to_save(['opened', phase], bug_data)

        pre_release_phase = True

        # Questions investigated:
        # 1. Which bugs saw their severity lowered before release (from blocker
        #    etc.)?
        # 2. Which bugs saw their severity increased after release (to blocker
        #    etc.)?
        severity_highest_index_before_release = None
        severity_index_at_release = None
        severity_highest_index_after_release = None

        # Current severity: could be changed, could be the initial value
        severity_current_index = SEVERITIES_LIST.index(bug_data['severity'])

        severity_index_last_processed = None

        # Look for changes to the 'severity' field and find the highest value
        # in the history.
        for historyItem in bug_data['history']:
            for change in historyItem['changes']:
                if change['field_name'] == 'severity':
                    change_time_str = historyItem['when']
                    change_time = datetime.datetime.strptime(change_time_str, '%Y-%m-%dT%H:%M:%SZ')
                    change_time = pytz.utc.localize(change_time)

                    severity_old = str(change['removed'])
                    severity_new = str(change['added'])
                    severity_index_old = SEVERITIES_LIST.index(severity_old)
                    severity_index_new = SEVERITIES_LIST.index(severity_new)

                    # Ignore changes which were made after the subsequent major release
                    if change_time > successor_release_date:
                        if severity_index_last_processed is None:
                            # Severity when the bug got created
                            severity_index_last_processed = severity_index_old
                        break

                    # Has the release shipped?
                    if pre_release_phase and change_time > release_date:
                        pre_release_phase = False
                        severity_index_at_release = severity_index_old
                        severity_highest_index_before_release = max(severity_highest_index_before_release, severity_index_old)
                        severity_highest_index_after_release = severity_index_new

                    # Before release
                    if pre_release_phase:
                        if severity_highest_index_before_release is None:
                            # Severity when the bug got created
                            severity_highest_index_before_release = severity_index_old
                        severity_highest_index_before_release = max(severity_highest_index_before_release, severity_index_new)
                    # After release
                    else:
                        if severity_highest_index_after_release is None:
                            severity_highest_index_after_release = severity_index_new
                        else:
                            severity_highest_index_after_release = max(severity_highest_index_after_release, severity_index_new)
        if severity_index_last_processed is None:
            severity_index_last_processed = severity_current_index
        if pre_release_phase:
            # Never a change to severity, current state is start state.
            if severity_highest_index_before_release is None:
                severity_highest_index_before_release = severity_index_last_processed
            if severity_index_at_release is None:
                severity_index_at_release = severity_index_last_processed
                severity_highest_index_after_release = severity_index_last_processed
        sev_before_release = SEVERITIES_LIST[severity_highest_index_before_release]
        sev_at_release = SEVERITIES_LIST[severity_index_at_release]
        sev_after_release = SEVERITIES_LIST[severity_highest_index_after_release]
        sev_group_before_release = SEVERITIES_GROUP_LIST.index(SEVERITIES[sev_before_release])
        sev_group_at_release = SEVERITIES_GROUP_LIST.index(SEVERITIES[sev_at_release])
        sev_group_after_release = SEVERITIES_GROUP_LIST.index(SEVERITIES[sev_after_release])
        # if sev_group_before_release > sev_group_at_release:
        #     print('bug severity decreased before release - bug', bug_data['id'])
        #     print('severity_highest_index_before_release', severity_highest_index_before_release)
        #     print('severity_index_at_release', severity_index_at_release)
        # if sev_group_before_release < sev_group_at_release:
        #     print('bug severity increased before release - bug', bug_data['id'])
        #     print('severity_highest_index_before_release', severity_highest_index_before_release)
        #     print('severity_index_at_release', severity_index_at_release)
        print(u'bug: {} - summary: {}'.format(bug_data['id'], bug_data['summary']))
        if sev_group_before_release > sev_group_at_release and sev_group_after_release > sev_group_at_release:
            print('bug: {}'.format(bug_data['id']))
            print('product: {}'.format(bug_data['product']))
            print('status_flag_version: {}'.format(bug_data[status_flag_version]))
            print('status_flag_successor_version: {}'.format(bug_data[status_flag_successor_version]))
            print('component: {}'.format(bug_data['component']))
            print('assignee email: {}'.format(bug_data['assigned_to_detail']['email']))
            print('summary: {}'.format(bug_data['summary']))
            sev_lowered_and_increased.append([
                                             bug_data['id'],
                                             bug_data['product'],
                                             bug_data[status_flag_version],
                                             bug_data[status_flag_successor_version],
                                             bug_data['component'],
                                             bug_data['assigned_to_detail']['email'],
                                             bug_data['summary'],
                                            ])
        if sev_group_after_release > sev_group_at_release:
            print('bug: {}'.format(bug_data['id']))
            print('product: {}'.format(bug_data['product']))
            print('status_flag_version: {}'.format(bug_data[status_flag_version]))
            print('status_flag_successor_version: {}'.format(bug_data[status_flag_successor_version]))
            print('component: {}'.format(bug_data['component']))
            print('assignee email: {}'.format(bug_data['assigned_to_detail']['email']))
            print('summary: {}'.format(bug_data['summary']))
            sev_increased_after_release.append([
                                                bug_data['id'],
                                                bug_data['product'],
                                                bug_data[status_flag_version],
                                                bug_data[status_flag_successor_version],
                                                bug_data['component'],
                                                bug_data['assigned_to_detail']['email'],
                                                bug_data['summary'],
                                              ])
        #    print('bug severity increased after release - bug', bug_data['id'])
        #    print('severity_highest_index_before_release', severity_highest_index_before_release)
        #    print('severity_highest_index_after_release', severity_highest_index_after_release)
        # if sev_group_after_release < sev_group_at_release:
        #     print('bug severity decreased after release - bug', bug_data['id'])
        #     print('severity_highest_index_before_release', severity_highest_index_before_release)
        #     print('severity_highest_index_after_release', severity_highest_index_after_release)

        creation = utils.get_date(bug_data['creation_time'])
        year, week, _ = creation.isocalendar()
        t = WFMT.format(year, week)
        sev_group_highest = SEVERITIES_GROUP_LIST[max(sev_group_before_release, sev_group_after_release)]
        data_opened[sev_group_highest][t] += 1

    sev_lowered_and_increased = []
    sev_increased_after_release = []

    weeks_opened = get_weeks(nightly_start, release_date)
    data_opened = {sev: {w: 0 for w in weeks_opened} for sev in set(SEVERITIES.values())}

    weeks_open_accum = get_weeks(nightly_start, successor_release_date)
    data_open_accum = {sev: {w: 0 for w in weeks_open_accum} for sev in set(SEVERITIES.values())}

    # Load Bugzilla data from file
    if bzdata_load_path:
        for bug_data in bugzilla_data_loaded['opened']['nightly']['data']:
            bug_handler(bug_data, data_opened)
    # Load Bugzilla data from Bugzilla server
    else:
        queries = []
        fields = [
                  'id',
                  'summary',
                  'product',
                  'component',
                  'creation_time',
                  'severity',
                  'assigned_to',
                  status_flag_version,
                  status_flag_successor_version,
                  'history'
                 ]

        nightly_params = {
            'include_fields': fields,
            'product': PRODUCTS_TO_CHECK,
            'f1': 'creation_ts',
            'o1': 'greaterthaneq',
            'v1': '',
            'f2': 'creation_ts',
            'o2': 'lessthan',
            'v2': '',
            'f3': 'bug_severity',
            'o3': 'notequals',
            'v3': 'enhancement',
            'f4': 'keywords',
            'o4': 'notsubstring',
            'v4': 'meta',
        }

        beta_params = {
            'include_fields': fields,
            'product': PRODUCTS_TO_CHECK,
            'f1': 'creation_ts',
            'o1': 'greaterthaneq',
            'v1': '',
            'f2': 'creation_ts',
            'o2': 'lessthan',
            'v2': '',
            'f3': 'bug_severity',
            'o3': 'notequals',
            'v3': 'enhancement',
            'f4': 'keywords',
            'o4': 'notsubstring',
            'v4': 'meta',
            'f5': status_flag_version,
            'o5': 'anyexact',
            'v5': 'affected, fix-optional, fixed, wontfix, verified, disabled',
        }

        phases = [
            {
                'name' : 'nightly',
                'query_params' : nightly_params,
                'start_date' : nightly_start,
                'end_date' : beta_start,
            },
            {
                'name' : 'beta',
                'query_params' : beta_params,
                'start_date' : beta_start,
                'end_date' : release_date,
            },
        ]
        for phase in phases:
            query_start = phase['start_date']
            # print('New phase')
            # print('query_start:', query_start)
            # print('end_date:', phase['end_date'])
            while query_start <= phase['end_date']:
                query_end = query_start + relativedelta(days=30)
                params = phase['query_params'].copy()

                # query_start <= creation_ts < query_end
                params['v1'] = query_start
                params['v2'] = min(query_end, phase['end_date'])
                
                logger.info('Bugzilla: From {} To {}'.format(query_start, query_end))

                queries.append(Bugzilla(params,
                                        bughandler=bug_handler,
                                        bugdata={
                                                 'phase' : phase['name'],
                                                 'data_opened' : data_opened,
                                                 'sev_lowered_and_increased' : sev_lowered_and_increased,
                                                 'sev_increased_after_release' : sev_increased_after_release,
                                                },
                                        timeout=960))
                query_start = query_end
                # print('query_start:', query_start)
                # print('end_date:', phase['end_date'])

        for q in queries:
            q.get_data().wait()

    y, w, _ = beta_start.isocalendar()
    data_opened['first_beta'] = WFMT.format(y, w)

    return (
            data_opened,
            sev_lowered_and_increased,
            sev_increased_after_release,
           )

def log(message):
    print(message)


def write_csv(major):
    (
     data_opened,
     sev_lowered_and_increased,
     sev_increased_after_release,
    ) = get_bugs(major)
    with open('data/bugs_count_{}.csv'.format(major), 'w') as Out:
        writer = csv.writer(Out, delimiter=',')

        y, w, _ = beta_start.isocalendar()
        first_beta_str = WFMT.format(y, w)
        writer.writerow(['First beta', first_beta_str])

        writer.writerow([])
        writer.writerow([])

        writer.writerow(['Opened bugs by week'])
        weeks = list(sorted(data_opened['normal'].keys()))
        head = ['Severity'] + weeks
        writer.writerow(head)
        for sev in ['blocker+critical+major', 'normal', 'minor+trivial']:
            numbers = data_opened[sev]
            numbers = [numbers[w] for w in weeks]
            writer.writerow([sev] + numbers)

        writer.writerow([])
        writer.writerow([])

        writer.writerow(['Bugs with severity significantly lowered before release and increased afterwards'])
        writer.writerow([
                         'Bug ID',
                         'Product',
                         'Status Version %'.format(major),
                         'Status Version %'.format(major + 1),
                         'Component',
                         'Assignee',
                         'Summary',
                       ])
        for row in sev_lowered_and_increased:
            writer.writerow(row)

        writer.writerow([])
        writer.writerow([])

        writer.writerow(['Bugs with severity significantly increased after release'])
        writer.writerow([
                         'Bug ID',
                         'Product',
                         'Status Version %'.format(major),
                         'Status Version %'.format(major + 1),
                         'Component',
                         'Assignee',
                         'Summary',
                       ])
        for row in sev_increased_after_release:
            writer.writerow(row)


parser = argparse.ArgumentParser(description='Count bugs created and fixed before release, by week')
parser.add_argument('product_version', type=int,
                    help='Firefox version')
parser.add_argument('--bzdata-load',
                    nargs='?',
                    default=argparse.SUPPRESS,
                    help='Load the Bugzilla data from a local JSON file. If no path is provided '
                         'the program will try to load "bugzilla_data_<versionnumber>.json" from the "data" folder.')
parser.add_argument('--bzdata-save',
                    nargs='?',
                    default=argparse.SUPPRESS,
                    help='Save the Bugzilla data to a local JSON file. If no path is provided '
                         'the program will try to save as "bugzilla_data_<versionnumber>.json" into the "data" folder.')
args = parser.parse_args()

# Firefox version for which the report gets generated.
product_version = args.product_version

# Bugzilla status flag for this version
status_flag_version = 'cf_status_firefox' + str(product_version)
status_flag_successor_version = 'cf_status_firefox' + str(product_version + 1)

# nightly_start is the date for the first nightly
# beta_start is the datetime the first beta build started (or now if no beta yet)
nightly_start, beta_start, release_date, successor_release_date = productdates.get_product_dates(product_version)

bzdata_load_path = None
if 'bzdata_load' in args:
    # Load Bugzilla data from file
    if args.bzdata_load:
        # File path provided as command line argument
        bzdata_load_path = args.bzdata_load
    else:
        # No file path provided, use default location
        bzdata_load_path = 'data/bugzilla_data_{}.json'.format(product_version)
    with open(bzdata_load_path, 'r') as bugzilla_data_reader:
        bugzilla_data_loaded = json.load(bugzilla_data_reader)
    log('Loaded Bugzilla data from {}'.format(bzdata_load_path))

bzdata_save_path = None
if 'bzdata_save' in args:
    # File path to which Bugzilla data shall be saved
    if args.bzdata_save:
        # File path provided as command line argument
        bzdata_save_path = args.bzdata_save
    else:
        # No file path provided, use default location
        bzdata_save_path = 'data/bugzilla_data_{}.json'.format(product_version)

write_csv(product_version)

if bzdata_save_path:
    # Save Bugzilla data to file
    with open('data/bugzilla_data_{}.json'.format(product_version), 'w') as bugzilla_data_writer:
        bugzilla_data_writer.write(json.dumps(bugzilla_data_to_save))
        log('Saved Bugzilla data to {}'.format(bzdata_save_path))

