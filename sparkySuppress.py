#!/usr/bin/env python3
from __future__ import print_function
from datetime import datetime
import configparser, time, json, sys, os, csv, requests, pytz, threading, validators
from urllib.parse import urlparse,parse_qs,quote
from distutils.util import strtobool

# Library https://github.com/JoshData/python-email-validator - see pip install instructions
from email_validator import validate_email, EmailNotValidError

# Global timeout value for API requests
T = 60

# Values permissible in .csv files. Not all have to be used.
flagNames = ('transactional', 'non_transactional')
fieldNames = ('recipient', 'type', 'source', 'description', 'created','updated','subaccount_id')

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
    print('    update               Uploads file contents to SparkPost.  Also checks and reports input problems as it runs.')
    print('    delete               Delete entries from SparkPost.  Also checks and reports input problems as it runs.')

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
def getSuppressionList(uri, apiKey, params, subaccount_id):
    try:
        path = uri + '/api/v1/suppression-list'
        h = {'Authorization': apiKey, 'Accept': 'application/json'}
        if subaccount_id:
            h['X-MSYS-SUBACCOUNT'] = str(subaccount_id)
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

def updateSuppressionList(recipBatch, uri, apiKey, subaccount_id):
    try:
        path = uri + '/api/v1/suppression-list'
        h = {'Authorization': apiKey, 'Content-Type': 'application/json', 'Accept': 'application/json'}
        if subaccount_id:
            h['X-MSYS-SUBACCOUNT'] = str(subaccount_id)
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
# Performance improvements: use 'threading' class for concurrent deletions (each API-call deletes one entry)
# and keep sessions persistent
#
class deleter(threading.Thread):
    def __init__(self, path, headers, s):
        threading.Thread.__init__(self)
        self.path = path
        self.headers = headers
        self.s = s
        self.res = None

    def run(self):
        moreToDo = True
        while moreToDo:
            self.res = self.s.delete(url=self.path, timeout=T, headers=self.headers)
            if self.res.status_code == 429:
                if self.res.json()['errors'][0]['message'] == 'Too many requests':
                    snooze = 10
                    print('.. pausing', snooze, 'seconds for rate-limiting')
                    time.sleep(snooze)
                    continue  # try again
            else:
                return

    def response(self):
        return(self.res)

#
# Class holding persistent requests session IDs
#
class persistentSession():
    def __init__(self, Nthreads):
        # Set up our connection pool
        self.svec = [None] * Nthreads
        for i in range(0, Nthreads):
            self.svec[i] = requests.session()
        self.n = Nthreads

    def id(self, i):
        return self.svec[i]

    def size(self):
        return self.n

#  Launch multi-threaded deletions.  URL-quote the recipient part
def threadAction(recipBatch, uri, apiKey, subaccount_id):
    assert len(recipBatch) <= persist.size()    # Check we have adequate connection pool
    th = [None] * persist.size()                # threads are created / destroyed each call
    h = {'Authorization': apiKey}
    if subaccount_id:
        h['X-MSYS-SUBACCOUNT'] = str(subaccount_id)
    for i,r in enumerate(recipBatch):
        path = uri + '/api/v1/suppression-list/' + quote(r['recipient'], safe='@')   # ensure forwardslash gets escaped
        s = persist.id(i)
        th[i] = deleter(path, h, s)
        th[i].start()                           # trigger the thread run method

    # Wait for the threads to come back
    doneCount = 0
    for i,r in enumerate(recipBatch):
        th[i].join(T + 10)  # Somewhat longer than the "requests" timeout
        res = th[i].response()
        if res.status_code == 204:
            doneCount += 1
        else:
            # Allow for responses that aren't valid JSON, as these have been seen in the wild
            try:
                print('  ',r['recipient'], 'Error:', res.status_code, ':', res.json())
            except:
                print('  ',r['recipient'], 'Raw Error:', res)
    return doneCount

def deleteSuppressionList(recipBatch, uri, apiKey, subaccount_id):
    doneCount = 0
    threadRecips = []                               # List collecting at most Nthreads recips
    print('Deleting {0} suppression list entries using {1} threads'.format(len(recipBatch), Nthreads))
    startT = time.time()                            # Measure time for batch
    # Collect recipients together into mini-batches that will be handled concurrently
    for r in recipBatch:
        threadRecips.append(r)
        if len(threadRecips) >= Nthreads:
            doneCount += threadAction(threadRecips, uri, apiKey, subaccount_id)
            threadRecips = []                       # Empty out, ready for next mini batch

    if len(threadRecips) > 0:                       # Handle the final mini batch, if any
        doneCount += threadAction(threadRecips, uri, apiKey, subaccount_id)

    endT = time.time()
    print('{0} entries deleted in {1:2.3f} seconds'.format(doneCount, endT - startT))
    return doneCount

# Functions that operate on on a batch - each has a row entry as input, and returns a count of entries
# actually transacted on SparkPost.
def noAction(r, uri, apiKey, subAccount):
    return 0

actionVector = {
    'check': noAction,
    'update': updateSuppressionList,
    'delete': deleteSuppressionList
}

# Functions to perform specific tasks on entire list
def RetrieveSuppListToFile(outfile, fList, baseUri, apiKey, subAccount, **p):
    if 'from' in p:
        print('Time from    :', p['from'])
    if 'to' in p:
        print('Time to      :', p['to'])
    if subAccount:
        print('Subaccount   :', subAccount)

    fh = csv.DictWriter(outfile, fieldnames=fList, restval='', extrasaction='ignore')
    fh.writeheader()
    suppPage = 1
    p['cursor'] = 'initial'
    morePages = True;
    while morePages:
        startT = time.time()                        # Measure time for each processing iteration
        res = getSuppressionList(uri=baseUri, apiKey=apiKey, params=p, subaccount_id = subAccount)
        if not res:                                 # Unexpected error - quit
            exit(1)

        for i in res['results']:
            fh.writerow(i)                          # Write out results as CSV rows in the output file
        endT = time.time()

        if p['cursor'] == 'initial':
            print('File fields  :', fList)
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

def processFile(infile, actionFunction, baseUri, apiKey, typeDefault, descDefault, batchSize, subAccount):
    if subAccount:
        print('Subaccount   :', subAccount)
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
    startT = time.time()                                # Measure overall checking time
    for r in f:
        l = f.line_num
        if f.line_num == 1:                             # Check if header row present
            if 'recipient' in r:                        # we've got an email header-row field - continue
                hdr = r
                for i, h in enumerate(hdr):
                    if not (h in fieldNames) and not (h in flagNames):
                        print('Unexpected .csv file field name found: ', h)
                        exit(1)
                continue                                # all done with this header line

            elif '@' in r[0] and len(r) == 1:           # Also accept headerless format with just email addresses
                hdr = ['recipient']                     # line 1 contains data - so we go on to process this
            else:
                print('Invalid .csv file header - must contain "recipient" field')
                exit(1)

        # Process lines containing entries
        if len(r) != len(hdr):
            print('  Line {0:8d} ! contains {1} fields, expecting {2} - stopping.'.format(f.line_num, len(r), len(hdr)))
            exit(1)

        # Parse values from the line of the file into a dict.  Takes column ordering from the header.
        row = {}
        for i, h in enumerate(hdr):
            r[i] = r[i].strip()                         # All fields are simple strings. Strip leading/trailing whitespace
            if r[i]:                                    # Collect only non-empty fields
                row[h] = r[i]

        # Now check semantics of this row's field contents
        recipOK = False
        if 'recipient' in row.keys():
            try:
                # don't check d12y, as too slow. Take the normalised version and force it to lower-case for our use
                v = validate_email(row['recipient'], check_deliverability=False)
                row['recipient'] = v['email'].lower()
                recipOK = True
            except EmailNotValidError as e:
                # email is not valid, exception message is human-readable
                print('  Line {0:8d} ! {1} {2}'.format(f.line_num, row['recipient'], str(e)))
                badRecips += 1

        flagsOK = False                                             # Starting assumption - we don't have good flags
        if 'type' in row.keys():
            row['type'] = stripQuotes(row['type'].lower())          # Clean up by lower-casing and stripping quotes, if any
            if row['type'] in flagNames:
                flagsOK = True
            else:
                print('  Line {0:8d} w invalid "type" = {1}, must be {2}'.format(f.line_num, row['type'], flagNames))
        else:
            # check for presence of older style flags (deprecated, but still acceptable).
            # Both must be present. If we can't convert to bool, flag error.
            if (flagNames[0] in row.keys()) and (flagNames[1] in row.keys()):
                try:
                    for i in flagNames:
                        row[i] = stripQuotes(row[i].title())        # Clean up by title-casing and stripping quotes, if any
                        row[i] = bool(strtobool(row[i]))            # in-place conversion to native bool type
                    flagsOK = True                                  # only if both convert OK
                except ValueError:
                    print('  Line {0:8d} w invalid {1} = {2}, must be true or false'.format(f.line_num, i, row[i]))
            else:
                print('  Line {0:8d} w need valid transactional & non_transactional flags: {1}'.format(f.line_num, row))

        addrsChecked += 1
        if flagsOK:
            goodFlags += 1
        else:
            row['type'] = typeDefault                               # Apply user-specified value
            defaultedFlags += 1

        if not 'description' in row:
            if descDefault:
                row['description'] = descDefault                    # Apply user-specified value

        # report, and filter out duplicate entries using set logic.
        # Note the same address with different new-style 'type' value (transactional / non-transactional) is distinct.
        if recipOK:
            if 'type' in row:
                u = (row['recipient'], row['type'])                 # new-style flags - make a tuple
            else:
                u = (row['recipient'], None)                        # old-style flags
            if u in seen:
                print('  Line {0:8d}   skipping duplicate {1}'.format(f.line_num, u))
                duplicateRecips += 1
            else:
                # This entry is good. Collect up into a batch, for more efficient API usage
                goodRecips += 1
                seen.add(u)
                recipBatch.append(row)
                if len(recipBatch) >= batchSize:
                    doneRecips += actionFunction(recipBatch, baseUri, apiKey, subAccount)
                    recipBatch = []                                 # Empty out, ready for next batch

    if len(recipBatch) > 0:                                         # Handle the final batch remaining, if any
        doneRecips += actionFunction(recipBatch, baseUri, apiKey, subAccount)
    endT = time.time()
    print('\nSummary:\n{0:8d} entries processed in {1:2.2f} seconds\n{2:8d} good recipients\n{3:8d} invalid recipients\n{4:8d} duplicates will be skipped\n{5:8d} done on SparkPost'
        .format(addrsChecked, endT-startT, goodRecips, badRecips, duplicateRecips, doneRecips))

    if actionFunction != deleteSuppressionList:
        print('\n{0:8d} with valid flags\n{1:8d} have type={2} default applied\n'
            .format(goodFlags, defaultedFlags, typeDefault))
    return True

# Check we have a valid SparkPost API endpoint URL
def checkValidSparkPostEndpoint(url):
    if str.startswith(url, 'https://'):
        fullurl = url
    else:
        fullurl = 'https://' + url                          # prepend the access method, if not already given

    if not validators.url(fullurl):
        print('Error: Host value malformed:', fullurl)
        if '#' in fullurl:
            print('NOTE: .ini file # comment character must be at beginning of line.')
        exit(1)
    else:
        # Just ping the bare endpoint, see if we get a text reply
        res = requests.get(fullurl)
        if res.status_code != 200 or not ('sparkpost' in res.text) :
            print('URL ',fullurl, 'not a valid SparkPost API endpoint')
            exit(1)
    # Otherwise OK
    return fullurl

# -----------------------------------------------------------------------------------------
# Main code
# -----------------------------------------------------------------------------------------
# Get parameters from .ini file
configFile = 'sparkpost.ini'
config = configparser.ConfigParser()
config.read_file(open(configFile))
cfg = config['SparkPost']
apiKey = cfg.get('Authorization', '')                   # API key is mandatory
if not apiKey:
    print('Error: missing Authorization line in ' + configFile)
    exit(1)

baseUri = checkValidSparkPostEndpoint(cfg.get('Host', 'api.sparkpost.com')) # If not specified, default to standard
print('Using SparkPost API endpoint:', baseUri)

timeZone = cfg.get('Timezone', 'UTC')                   # If not specified, default to UTC

properties = cfg.get('Properties', 'recipient,type,description')        # If the fields are not specified, default
properties = properties.replace('\r', '').replace('\n', '')             # Strip newline and CR
fList = properties.split(',')

batchSize = cfg.getint('BatchSize', 10000)              # Use default batch size if not given in the .ini file

typeDefault = cfg.get('TypeDefault', 'non_transactional')   # default applied to updates, if file doesn't contain type
if not(typeDefault in flagNames):
    print('Invalid .ini file setting typeDefault = ', typeDefault, 'Must be', flagNames)
    exit(1)

descDefault = cfg.get('DescriptionDefault')

charEncs = cfg.get('FileCharacterEncodings', 'utf-8').split(',')

Nthreads = cfg.getint('DeleteThreads', 10)
persist = persistentSession(Nthreads)                   # hold a set of persistent 'requests' sessions

subAccount = cfg.getint('SubAccount', 0)                # Default 0 means 'master account'

if len(sys.argv) >= 3:
    cmd = sys.argv[1]
    suppFname = sys.argv[2]

    if cmd in actionVector.keys():
            # Scan all lines in the file, looking for character encoding that works
            for ce in charEncs:
                with open(suppFname, 'r', newline='', encoding=ce) as infile:
                    try:
                        l = 1                           # Keep line number available in exception scope for ease of reporting
                        print('Trying file', suppFname, 'with encoding:', ce)
                        for c in infile:
                            l += 1
                        break                           # Successfully read all lines - on to the next stage

                    except Exception as e:
                        # If the file contains character set encoding anomalies we can't recover. At least provide helpful line number output
                        print('\tNear line', l, e)

            print('\tFile reads OK.\n\nLines in file:', l)
            with open(suppFname, 'r', newline='', encoding=ce) as infile:
                if cmd=='delete':                       # keep batch sizes small for Delete, so we can see visible progress
                    batchSize = min(batchSize, 10*Nthreads)
                processFile(infile, actionVector[cmd], baseUri, apiKey, typeDefault, descDefault, batchSize, subAccount)

    elif cmd=='retrieve':
        with open(suppFname, 'w', newline='', encoding=charEncs[0]) as outfile:
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

                opts = {'from': fromTime, 'to': toTime, 'per_page': batchSize}      # Need this way, as 'from' is a Python keyword
            else:
                opts = {'per_page': batchSize}
            print('Retrieving SparkPost suppression-list entries to', suppFname, 'with file encoding=' + charEncs[0])
            RetrieveSuppListToFile(outfile, fList, baseUri, apiKey, subAccount, **opts)

    else:
        printHelp()
        exit(1)
else:
    printHelp()