#!/usr/bin/env python3
from __future__ import print_function
from datetime import datetime
import configparser, time, json, sys, os, csv, requests, pytz, threading
import urllib3, certifi
from urllib.parse import urlparse,parse_qs,quote
from distutils.util import strtobool

# Library https://github.com/JoshData/python-email-validator - see pip install instructions
from email_validator import validate_email, EmailNotValidError

T = 60                  # Global timeout value for API requests
flagNames = ('transactional', 'non_transactional')

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
    print('    cmd                  check|retrieve|update|delete')
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
    print('    update               Uploads file contents to SparkPost.  Also runs checks.')
    print('    delete               Delete entries from SparkPost.  Also runs checks,')

# Validate our input time format, which for simplicity is just to 1 minute resolution without timezone offset.
def isExpectedEventDateTimeFormat(timestamp):
    format_string = '%Y-%m-%dT%H:%M'
    try:
        datetime.strptime(timestamp, format_string)
        return True
    except ValueError:
        return False

# Take a naive time value, compose it with the named timezone, giving a datetime with numeric TZ offset.
# The offset will vary with DST depending on your locale / time of year.
def composeEventDateTimeFormatWithTZ(t, tzName):
    format_string = '%Y-%m-%dT%H:%M'
    td = pytz.timezone(tzName).localize(datetime.strptime(t, format_string))
    tstr = t+':00'+td.strftime('%z')          # Compose with seconds field and timezone field
    return tstr

# Strip initial and final quotes from strings, if present (either single, or double quotes in pairs)
def stripQuotes(s):
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    return s

# API access functions - see https://developers.sparkpost.com/api/suppression-list.html
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

def updateSuppressionList(recipBatch, uri, apiKey):
    try:
        path = uri + '/api/v1/suppression-list'
        h = {'Authorization': apiKey, 'Content-Type': 'application/json', 'Accept': 'application/json'}
        body = json.dumps({'recipients': recipBatch})
        print('Updating {0:6d} entries to SparkPost'.format(len(recipBatch)), end=' ', flush=True)

        startT = time.time()                        # Measure time for each processing iteration
        response = requests.put(path, timeout=T, headers=h, data=body)      # Params not needed
        endT = time.time()
        print('in {0:2.3f} seconds'.format(endT - startT))
        if response.status_code == 200:
            return len(recipBatch)
        else:
            print('Error:', response.status_code, ':', response.text)
            # If we do get an error - might be solvable. Split batch into two halves and retry
            if len(recipBatch) < 2:
                print('Error - could not update: ', body)
                return 0                                    # Give up
            else:
                half = len(recipBatch) // 2                 # integer division
                r1 = updateSuppressionList(recipBatch[:half], uri, apiKey)
                r2 = updateSuppressionList(recipBatch[half:], uri, apiKey)
                return r1 + r2                              # indicate successful entries done

    except ConnectionError as err:
        print('error code', err.status_code)
        exit(1)

#
# Performance improvement: use 'threading' class for concurrent deletions (each API-call deletes one entry)
class deleter(threading.Thread):
    def __init__(self, path, headers, s):
        threading.Thread.__init__(self)
        self.path = path
        self.headers = headers
        self.s = s
        self.res = None

    def run(self):
        s = self.s
        self.res = s.request('DELETE', url=self.path, timeout=T, headers=self.headers)

    def response(self):
        return(self.res)

Nthreads = 10
svec = [None] * Nthreads                                # Persistent sessions

#  Launch multi-threaded deletions.  URL-quote the recipient part.
def threadAction(recipBatch, uri, apiKey):
    assert len(recipBatch) <= Nthreads
    # Set up our connection pool
    if None in svec:
        for i,v in enumerate(svec):
            svec[i] = urllib3.PoolManager(cert_reqs = 'CERT_REQUIRED', ca_certs = certifi.where() )
    th = [None] * Nthreads  # Init empty array
    h = {'Authorization': apiKey}

    for i,r in enumerate(recipBatch):
        path = uri + '/api/v1/suppression-list/' + quote(r['recipient'])
        th[i] = deleter(path, h, svec[i])
        th[i].start()

    # Wait for the threads to come back
    doneCount = 0
    for i,r in enumerate(recipBatch):
        th[i].join(T + 10)  # Somewhat longer than the "requests" timeout
        res = th[i].response()
        if res.status == 204:
            doneCount += 1
        else:
            print(r['recipient'], 'Error:', res.status, ':', json.loads(res.data.decode('utf-8')))
    return doneCount

def deleteSuppressionList(recipBatch, uri, apiKey):
    doneCount = 0
    threadRecips = []                               # List collecting at most Nthreads recips
    startT = time.time()                            # Measure time for batch
    # Collect recipients together into mini-batches that will be handled concurrently
    for r in recipBatch:
        threadRecips.append(r)
        if len(threadRecips) >= Nthreads:
            threadAction(threadRecips, uri, apiKey)
            threadRecips = []                       # Empty out, ready for next mini batch

    if len(threadRecips) > 0:                       # Handle the final mini batch, if any
        threadAction(threadRecips, uri, apiKey)

    endT = time.time()
    print('{0} entries deleted in {1:2.3f} seconds'.format(doneCount, endT - startT))
    return doneCount

# Functions that operate on on a batch - each has a row entry as input, and returns a count of entries
# actually transacted on SparkPost.
def noAction(r, uri, apiKey):
    return 0

actionVector = {
    'check': noAction,
    'update': updateSuppressionList,
    'delete': deleteSuppressionList
}

# Functions to perform specific tasks on entire list
def RetrieveSuppListToFile(outfile, fList, baseUri, apiKey, **p):
    fh = csv.DictWriter(outfile, fieldnames=fList, restval='', extrasaction='ignore')
    fh.writeheader()
    suppPage = 1
    p['cursor'] = 'initial'
    morePages = True;
    while morePages:
        startT = time.time()                        # Measure time for each processing iteration
        res = getSuppressionList(uri=baseUri, apiKey=apiKey, params=p)
        if not res:                                 # Unexpected error - quit
            exit(1)

        for i in res['results']:
            fh.writerow(i)                          # Write out results as CSV rows in the output file
        endT = time.time()

        if p['cursor'] == 'initial':
            print('File fields: ', fList)
            print('Total entries to fetch: ', res['total_count'])
        print('Page {0:8d}: got {1:6d} entries in {2:2.3f} seconds'.format(suppPage, len(res['results']), endT - startT))

        # Get the links from the response.  If there is a 'next' link, we continue processing
        morePages = False
        for l in res['links']:
            if l['rel'] == 'next':
                p['cursor'] = parse_qs(urlparse(l['href']).query)['cursor']
                suppPage += 1
                morePages=True
            elif l['rel'] in ('last', 'first', 'previous'):
                pass
            else:
                print('Unexpected link in response: ', json.dumps(l))
                exit(1)

def processFile(infile, actionFunction, baseUri, apiKey, typeDefault, **p):
    f = csv.reader(infile)
    addrsChecked = 0
    goodRecips = 0
    duplicateRecips = 0
    badRecips = 0
    doneRecips = 0
    goodFlags = 0
    defaultedFlags = 0
    seen = set()
    recipBatch = []
    startT = time.time()  # Measure overall checking time
    for r in f:
        l = f.line_num
        if f.line_num == 1:  # Check if header row present
            if 'recipient' in r:  # we've got an email header-row field - continue
                hdr = r
                continue
            elif '@' in r[0] and len(r) == 1:  # Also accept headerless format with just email addresses
                hdr = ['recipient']  # line 1 contains data - so continue processing
            else:
                print('Invalid .csv file header - must contain "recipient" field')
                exit(1)

        # Parse values from the line of the file into a dict.  Allows for column ordering to vary.
        row = {}
        for i, h in enumerate(hdr):
            if r[i]:  # Only parse non-empty fields from this line.  Also accept older flagNames columns
                if (h in ('recipient', 'type', 'description')) or (h in flagNames):
                    row[h] = r[i].strip()               # all fields are simple strings - strip leading/trailing whitespace
                else:
                    print('Unexpected .csv file field name found: ', h)
                    exit(1)

        # Now check this row's contents
        recipOK = False
        if 'recipient' in row:
            try:
                v = validate_email(row['recipient'], check_deliverability=False)  # don't check d12y, as too slow
                # Take the normalised version and force it to lower-case .. suppression list does not like mixed case
                row['recipient'] = v['email'].lower()
                recipOK = True
            except EmailNotValidError as e:
                # email is not valid, exception message is human-readable
                print('  Line {0:8d} ! {1} {2}'.format(f.line_num, row['recipient'], str(e)))
                badRecips += 1

        flagsOK = False
        if 'type' in row:
            row['type'] = stripQuotes(row['type'].lower())          # Clean up by lower-casing and stripping quotes
            if row['type'] in flagNames:
                flagsOK = True
            else:
                print('  Line {0:8d} w invalid "type" = {1}, must be {2}'.format(f.line_num, row['type'], flagNames))

        for i in flagNames:                                         # older style flags (deprecated, but still valid)
            if i in row:
                row[i] = stripQuotes(row[i].title())                # Clean up by title-casing and stripping quotes
                try:
                    row[i] = bool(strtobool(row[i]))                # in-place replacement with bool type
                    flagsOK = True
                except ValueError:
                    print('  Line {0:8d} w invalid {1} = {2}, must be true or false'.format(f.line_num, i, row[i]))
                    flagsOK = False

        addrsChecked += 1
        if flagsOK:
            goodFlags += 1
        else:
            # Apply user-specified default using the new flag type - also purge any old-style flags
            for i in flagNames:
                if i in row:
                    del row[i]
            row['type'] = typeDefault                               # Apply user-specified value
            defaultedFlags += 1

        if recipOK:
            # construct a tuple for 'already seen' checking logic that includes 'type' flag, if given
            if 'type' in row:
                u = (row['recipient'], row['type'])
            else:
                u = (row['recipient'], None)
            if u in seen:
                print('  Line {0:8d}   skipping duplicate {1}'.format(f.line_num, u))
                duplicateRecips += 1
            else:
                goodRecips += 1
                seen.add(u)                         # TODO: handle flag-awareness on dups
                recipBatch.append(row)              # Build up batches, for more efficient API usage
                if len(recipBatch) >= p['per_page']:
                    doneRecips += actionFunction(recipBatch, baseUri, apiKey)
                    recipBatch = []                 # Empty out, ready for next batch

    if len(recipBatch) > 0:                     # Handle the final batch remaining, if any
        doneRecips += actionFunction(recipBatch, baseUri, apiKey)
    endT = time.time()
    print('\nSummary:\n{0:8d} entries processed in {1:2.2f} seconds\n{2:8d} good recipients\n{3:8d} invalid recipients\n{4:8d} duplicates will be skipped\n{5:8d} done on SparkPost'
        .format(addrsChecked, endT-startT, goodRecips, badRecips, duplicateRecips, doneRecips))
    print('\n{0:8d} with valid flags\n{1:8d} have type={2} default applied\n'
        .format(goodFlags, defaultedFlags, typeDefault))
    return True

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

timeZone = cfg.get('Timezone', 'UTC')                   # If not specified, default to UTC

properties = cfg.get('Properties', 'recipient,type,description')        # If the fields are not specified, default
properties = properties.replace('\r', '').replace('\n', '')             # Strip newline and CR
fList = properties.split(',')

batchSize = cfg.getint('BatchSize', 10000)              # Use default batch size if not given in the .ini file

typeDefault = cfg.get('TypeDefault')
if not(typeDefault in flagNames):
    print('Invalid .ini file setting typeDefault = ', typeDefault, 'Must be', flagNames)
    exit(1)

# Try the configured character encodings in order
charEncs = cfg.get('FileCharacterEncodings', 'utf-8').split(',')

if len(sys.argv) >= 3:
    cmd = sys.argv[1]
    suppFname = sys.argv[2]

    if cmd in actionVector.keys():
            # Scan all lines in the file, looking for character encoding that works
            for ce in charEncs:
                with open(suppFname, 'r', newline='', encoding=ce) as infile:
                    try:
                        l = 1                                   # Keep line number available in exception scope for ease of reporting
                        print('Trying file', suppFname, 'with encoding:', ce)
                        for c in infile:
                            l += 1
                        break                                   # Successfully read all lines - on to the next stage

                    except Exception as e:
                        # If the file contains character set encoding anomalies we can't recover. At least provide helpful line number output
                        print('\tNear line', l, e)

            print('\tFile reads OK.\n\nLines in file:', l)
            with open(suppFname, 'r', newline='', encoding=ce) as infile:
                processFile(infile, actionVector[cmd], baseUri, apiKey, typeDefault, per_page=batchSize)

    elif cmd=='retrieve':
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
                opts = {'from': fromTime, 'to': toTime, 'per_page': batchSize}      # Need this way, as 'from' is a Python keyword
                RetrieveSuppListToFile(outfile, fList, baseUri, apiKey, **opts)
            else:
                print('Retrieving SparkPost suppression-list entries (any time-range) to', suppFname)
                RetrieveSuppListToFile(outfile, fList, baseUri, apiKey, per_page=batchSize)
    else:
        printHelp()
        exit(1)
else:
    printHelp()