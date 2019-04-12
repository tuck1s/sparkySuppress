# sparkySuppress
SparkPost suppression-list management tool:
- Check format of your files (prior to import)
- Retrieve back from SparkPost
- Update to SparkPost (aka create if your list is currently empty)
- Delete from SparkPost

This is useful in the following scenarios:
- Migrating from another provider towards SparkPost. 
Always a good idea from a deliverability standpoint to bring your suppressions with you.
- Getting suppressions back into your upstream campaign management tool.
- Cleaning your suppression list out. Perhaps a rare requirement but it's there for you.

## Pre-requisites

Firstly ensure you have `python3`, `pip` and `git`.

## Easy installation

Next, get the project. Install `pipenv`, and use this to install the project dependencies.
```
git clone https://github.com/tuck1s/sparkySuppress.git
cd sparkySuppress
pip install --user pipenv
pipenv install
pipenv shell
```

Note: In the above commands, you may need to run `pip3` instead of `pip`.

You can now type `./sparkySuppress.py` and see usage info.

Rename `sparkpost.ini.example` to `sparkpost.ini`, and insert your API key.

## Usage
```
$ ./sparkySuppress.py 

NAME
   ./sparkySuppress.py
   Manage SparkPost customer suppression list.

SYNOPSIS
  ./sparkySuppress.py cmd supp_list [from_time to_time]


MANDATORY PARAMETERS
    cmd                  check|retrieve|update|delete
    supp_list            .CSV format file, containing as a minimum the email recipients
                         Full example file format available from https://app.sparkpost.com/lists/suppressions

OPTIONAL PARAMETERS
    from_time            } for retrieve only
    to_time              } Format YYYY-MM-DDTHH:MM

COMMANDS
    check                Validates the format of a file, checking that email addresses are well-formed, but does not upload them.
    retrieve             Gets your current suppression-list contents from SparkPost back into a file.
    update               Uploads file contents to SparkPost.  Also checks and reports input problems as it runs.
    delete               Delete entries from SparkPost.  Also checks and reports input problems as it runs.

```

## Preparing input files for update
As a minimum, the input file contains a list of email addresses:
```csv
billybob@gmail.com
alice@hotmail.com
```

The file can also have the full [SparkPost template format](https://app.sparkpost.com/lists/suppressions), for example:
```csv
recipient,type,description
anon82133016@demo.sink.sparkpostmail.com,transactional,Example data import
anon15505125@demo.sink.sparkpostmail.com,transactional,Example data import
```

## Example output
Checking a file (that has an error in it)
```
$ ./sparkySuppress.py check 1klist-with-error.csv
Trying file 1klist-with-error.csv with encoding: utf-8
        File reads OK.

Lines in file: 1002
  Line        2 ! bad@email@address.com The email address is not valid. It must have exactly one @-sign.
  Line        3 ! invalid.email@~{}gmail.com The domain name ~{}gmail.com contains invalid characters (Codepoint U+007E not allowed at position 1 in '~{}gmail.com').

Summary:
    1000 entries processed in 0.27 seconds
     998 good recipients
       2 invalid recipients
       0 duplicates will be skipped
       0 done on SparkPost

    1000 with valid flags
       0 have type=non_transactional default applied

```

Updating SparkPost from a file:
```
$ ./sparkySuppress.py update 100klist.csv 
Trying file 100klist.csv with encoding: utf-8
        File reads OK.

Lines in file: 100002
Updating  10000 entries to SparkPost in 7.552 seconds
Updating  10000 entries to SparkPost in 8.805 seconds
:
:

Summary:
  100000 entries processed in 108.72 seconds
  100000 good recipients
       0 invalid recipients
       0 duplicates will be skipped
  100000 done on SparkPost

  100000 with valid flags
       0 have type=non_transactional default applied

```
API calls are batched for efficiency (batch size is configurable).

Retrieving a file copy of your SparkPost suppression list:
```
$ ./sparkySuppress.py retrieve getback.csv
Retrieving SparkPost suppression-list entries to getback.csv with file encoding=utf-8
File fields  : ['recipient', 'type', 'description']
Total entries to fetch:  101450
Page        1: got  10000 entries in 4.002 seconds
Page        2: got  10000 entries in 3.604 seconds
:
:
```

Retrieve entries from/to specific creation times (as per API docs):
```
$ ./sparkySuppress.py retrieve mylist.csv 2017-08-11T17:00 2017-08-11T18:00
Retrieving SparkPost suppression-list entries to mylist.csv with file encoding=utf-8
Time from    : 2017-08-11T17:00:00+0100
Time to      : 2017-08-11T18:00:00+0100
File fields  : ['recipient', 'type', 'description']
Total entries to fetch:  1
Page        1: got      1 entries in 1.287 seconds
```

Deleting entries from your SparkPost suppression list:
```
./sparkySuppress.py delete 1klist.csv
Trying file 1klist.csv with encoding: utf-8
        File reads OK.

Lines in file: 1002
Deleting 100 suppression list entries using 10 threads
100 entries deleted in 3.834 seconds
:
:
```
Delete uses multi-threading for best performance (configurable), coupled with smaller batch size of 10*threads.

## .ini file parameters
Minimum requirement is:
```ini
[SparkPost]
Authorization = <YOUR API KEY>
```

Full set of options are as per `sparkpost.ini.example` file included in this project:

```ini
[SparkPost]
Authorization = <YOUR API KEY>

# Host specifier is only required for Enterprise service
#Host = demo.api.e.sparkpost.com

# Optional. What properties to put in files retrieved from SparkPost. Useful to keep file sizes down if you don't need all fields.
# If omitted, defaults to recipient, type, description.
#Properties = recipient,type,source,description,created,updated,subaccount_id

# Optional. Timezone that from_time and to_time retrieve queries apply to.  If omitted, defaults to UTC.
#Timezone = Europe/London

# Optional.  If omitted, defaults to 10000 for updates and retrieves. Lower number means make more, smaller-sized, API requests.
#BatchSize = 10000

# Optional, if omitted defaults to 10. Number of parallel threads to run on Deletes.
# Because Delete API is one-at-a-time, runs faster with more threads/http sessions.
# Increase with caution, too many threads can stress your host, and will likely cause rate-limiting from SparkPost, which
# defeats the object of going faster!
#DeleteThreads = 10

# Optional. Work within a subaccount.  If omitted, defaults to the master account (=0).
#Subaccount = 2

# Optional. File encodings used for reading your file during check/update/delete/retrieve.
# Default value is utf-8. You can give a list of encodings to try, the tool will try each in turn until the file
# reads properly.
# If your file fails to read, check possible values to use on: https://docs.python.org/3.6/library/codecs.html#standard-encodings
# For hints, see also: http://python-notes.curiousefficiency.org/en/latest/python3/text_file_processing.html
#
# For retrieve, the first encoding in the list is used to write the file.
FileCharacterEncodings=utf-8,utf-16,ascii,latin-1

# Optional. When updating with basic lists of email addresses that lack flags, apply the following default type.
# if omitted, non_transactional will be used.
TypeDefault = non_transactional

# Optional. Add the following description to your imports.
DescriptionDefault = sparkySuppress import

# Optional. Tune the snooze time used when 429 rate-limiting replies received. If omitted, defaults to 10 seconds.
# SnoozeTime = 2
```

The `Host` address can begin with `https://`. If omitted, this will be added by the tool. The tool now checks the address is well-formed and
is a valid SparkPost API endpoint.

`Timezone` is used to localise the from/to search times.  Applies to `retrieve` only.  Uses the [pytz](http://pytz.sourceforge.net/#)
library to accept human-readable timezone names from over 500 entries in the [Olson Database.](https://en.wikipedia.org/wiki/Tz_database)
`US/Eastern`, `America/New_York`, `America/Los_Angeles`, `Europe/London` are valid examples.

The search times can naturally cross a DST threshold. For example, in `Timezone = America/New_York` you might request:
```
$ ./sparkySuppress.py retrieve mylist.csv 2017-03-01T00:00 2017-04-05T23:59
Retrieving SparkPost suppression-list entries to mylist.csv with file encoding=utf-8
Time from    : 2017-03-01T00:00:00-0500
Time to      : 2017-04-05T23:59:00-0400
```
The offset is applied taking DST into account for those dates.

`Subaccount` applies as a filter to the retrieve command, and provides a default for the update and delete commands, for example:
```
./sparkySuppress.py update 1klist.csv 
Trying file 1klist.csv with encoding: utf-8
	File reads OK.

Lines in file: 1002
Subaccount   : 2
Updating   1000 entries to SparkPost in 5.661 seconds
:
```

`Properties` controls the columns written in retrieved files, and can give you the `subaccount_id` for each entry.

### Subaccount handling for update and delete

The .csv file `subaccount_id` field, when present in the file header
and defined in an entry, takes precedence over the .ini file `Subaccount` setting (which is used as a default).

For efficiency `update` splits into sub-batches, then makes each API call with common `subaccount_id` values.

### Input file character encodings preference

`FileCharacterEncodings` is a cool feature - the tool will attempt to read your input files using encodings
in the order given. For example, many files output from Excel will be in Latin-1, rather than the more
universal UTF-8. The tool will attempt to read your file using each encoding, and if it finds anomalies, will
try in the next encoding and so on.  Example:
```
$ ./sparkySuppress.py check klist-1.csv
Trying file klist-1.csv with encoding: utf-8
        Near line 1125 'utf-8' codec can't decode byte 0x9a in position 7198: invalid start byte
Trying file klist-1.csv with encoding: utf-16
        Near line 1 UTF-16 stream does not start with BOM
Trying file klist-1.csv with encoding: ascii
        Near line 1125 'ascii' codec can't decode byte 0x9a in position 7198: ordinal not in range(128)
Trying file klist-1.csv with encoding: latin-1
        File reads OK.

Lines in file: 8496
:
:
```

For the `retrieve` command, file *outputs* are in UTF-8 encoding.

## Performance considerations
Update and retrieve commands are efficient with larger batch sizes. The default of 10000 is ideal, unless you have
difficulties with large https transfers to/from your host machine.

The check command validates email addresses for RFC-compatibility. It does not check the domains are deliverable,
as a) you might want to suppress undeliverable addresses and b) the checks would be *much* slower if we did.

The delete command uses Python `threading` to speed things up. You can tune this using the .ini file parameter `DeleteThreads`. Setting
value too high could stress your machine and will cause SparkPost to rate-limit. 10 threads is about right.  If you see delete problems,
you can try setting threads to 1.

## See also
[Using Suppression Lists](https://support.sparkpost.com/customer/portal/articles/1929891)

[Alternative method using the SparkPost UI - uploading and Storing a Recipient List as a CSV file](https://support.sparkpost.com/customer/portal/articles/2351320)

[Generating test suppression lists](https://www.sparkpost.com/blog/recipient-suppression-lists-python/)

This tool makes use of this external [library for email address checking.](https://github.com/JoshData/python-email-validator)
