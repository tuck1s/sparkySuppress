#!/usr/bin/env python3
from __future__ import print_function
from datetime import datetime
import configparser, time, json, sys, os, csv, requests, pytz
from urllib.parse import urlparse,parse_qs

T = 60                  # Global timeout value for API requests

def printHelp():
    progName = sys.argv[0]
    shortProgName = os.path.basename(progName)
    print('\nNAME')
    print('   ' + progName)
    print('   Manage SparkPost customer suppression list.\n')
    print('SYNOPSIS')
    print('  ./' + shortProgName + ' cmd supp_list [from_time to_time]\n')
    print('')
    print('MANDATORY PARAMETERS')
    print('    cmd                  check|retrieve|update')
    print('    supp_list            .CSV format file, containing as a minimum the email recipients')
    print('                         Full example file format available from https://app.sparkpost.com/lists/suppressions')
    print('')
    print('OPTIONAL PARAMETERS')
    print('    from_time            } for retrieve only')
    print('    to_time              } Format YYYY-MM-DDTHH:MM')
    print('')
    print('COMMANDS')
    print('    check                Validates the format of a file, checking that email addresses are well-formed, but does not upload them.')
    print('    retrieve             Gets your current suppression-list contents from SparkPost back into a file.')
    print('    update               Uploads file contents to SparkPost.  Also verifies as "check" does.')

# Validate our inpput time format, which for simplicity is just to 1 minute resolution without timezone offset.
def isExpectedEventDateTimeFormat(timestamp):
    format_string = '%Y-%m-%dT%H:%M'
    try:
        datetime.strptime(timestamp, format_string)
        return True
    except ValueError:
        return False

# Take a naive time value, and compose it with the named timezone, giving a time with numeric TZ offset.
# Owing to DST, the offset may vary on the time of year.
def composeEventDateTimeFormatWithTZ(t, tzName):
    format_string = '%Y-%m-%dT%H:%M'
    td = pytz.timezone(tzName).localize(datetime.strptime(t, format_string))
    tstr = t+':00'+td.strftime('%z')          # Compose with seconds field and timezone field
    return tstr

# Get suppression list entries, using API call - documentation at
# https://developers.sparkpost.com/api/suppression-list.html#suppression-list-search-get
def getSuppressionList(uri, apiKey, params):
    try:
        path = uri + '/api/v1/suppression-list'
        h = {'Authorization': apiKey, 'Accept': 'application/json'}
        moreToDo = True
        while moreToDo:
            response = requests.get(path, timeout=T, headers=h, params=params)

            # Handle possible 'too many requests' error inside this module
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                if response.json()['errors'][0]['message'] == 'Too many requests':
                    snooze = 120
                    print('.. pausing', snooze, 'seconds for rate-limiting')
                    time.sleep(snooze)
                    continue                # try again
            else:
                print('Error:', response.status_code, ':', response.text)
                return None

    except ConnectionError as err:
        print('error code', err.status_code)
        exit(1)

# -----------------------------------------------------------------------------------------
# Main code
# -----------------------------------------------------------------------------------------
# Get parameters from .ini file
configFile = 'sparkpost.ini'
config = configparser.ConfigParser()
config.read_file(open(configFile))
cfg = config['SparkPost']
apiKey = cfg.get('Authorization', '')           # API key is mandatory
if not apiKey:
    print('Error: missing Authorization line in ' + configFile)
    exit(1)
baseUri = 'https://' + cfg.get('Host', 'api.sparkpost.com')

timeZone = cfg.get('Timezone', 'UTC')           # If not specified, default to UTC

properties = cfg.get('Properties', 'recipient,type,description')        # If the fields are not specified, default
properties = properties.replace('\r', '').replace('\n', '')             # Strip newline and CR
fList = properties.split(',')

batchSize = cfg.getint('BatchSize', 10000)      # Use default batch size if not given in the .ini file

if len(sys.argv) >= 2:
    cmd = sys.argv[1]
    suppFname = sys.argv[2]

    if cmd == 'check':
        pass
    elif cmd == 'retrieve':
        with open(suppFname, 'w', newline='') as outfile:
            # Check for optional time-range parameters
            fromTime = None;
            toTime = None;
            if len(sys.argv) >= 4:
                fromTime = sys.argv[3]
                if not isExpectedEventDateTimeFormat(fromTime):
                    print('Error: unrecognised from_time:', fromTime)
                    exit(1)
                fromTime = composeEventDateTimeFormatWithTZ(fromTime, timeZone)

                toTime = sys.argv[4]
                if not isExpectedEventDateTimeFormat(toTime):
                    print('Error: unrecognised to_time:', toTime)
                    exit(1)
                toTime = composeEventDateTimeFormatWithTZ(toTime, timeZone)

                print('Retrieving SparkPost suppression-list entries from ' + fromTime + ' to ' + toTime + ' ' + timeZone + ' to', suppFname)
            else:
                print('Retrieving SparkPost suppression-list entries (any time-range) to', suppFname)

            fh = csv.DictWriter(outfile, fieldnames=fList, restval='', extrasaction='ignore')
            fh.writeheader()
            print('Properties: ', fList)
            morePages = True;
            suppPage = 1
            p = {
                'cursor': 'initial',
                'per_page': batchSize,
            }
            if toTime and fromTime:
                p.update({
                    'from': fromTime,
                    'to': toTime,
                })
            while morePages:
                startT = time.time()                        # Measure time for each processing iteration
                res = getSuppressionList(uri=baseUri, apiKey=apiKey, params=p)
                if not res:                                 # Unexpected error - quit
                    exit(1)
                for i in res['results']:
                    fh.writerow(i)                          # Write out results as CSV rows in the output file
                endT = time.time()

                if p['cursor'] == 'initial':
                    print('Total entries to fetch: ', res['total_count'])
                print('Page {0:8d}: got {1:6d} events in {2:2.3f} seconds'.format(suppPage, len(res['results']), endT - startT))

                # Get the links from the response.  If there is a 'next' link, we continue processing
                morePages = False
                for l in res['links']:
                    if l['rel'] == 'next':
                        p['cursor'] = parse_qs(urlparse(l['href']).query)['cursor']
                        suppPage += 1
                        morePages=True
                    elif l['rel'] == 'last' or l['rel'] == 'first' or l['rel'] == 'previous':
                        pass
                    else:
                        print('Unexpected link in response: ', json.dumps(l))
                        exit(1)

    elif cmd == 'update':
        pass
    elif cmd == 'delete':
        pass
        # deletes have to be done one by one
    else:
        printHelp()
        exit(1)

else:
    printHelp()
