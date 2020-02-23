#!/usr/bin/env python3
# Licensed under GPLv3
import datetime
import argparse
import calendar
import os.path
import decimal
import string
import json
import sys
import csv
import os
import re
from io import StringIO

# TODO:
# - provide a way to mark a transation as "predicted future" and render these
#   transaction visually distinct


# Ensure that we look for any modules in our local lib dir.  This allows simple
# testing and development use.  It also does not break the case where the lib
# has been installed properly on the normal sys.path
sys.path.insert(0,
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'lib'))
# I would use site.addsitedir, but it does an append, not insert

# Stupid pyflake, neither of these imports can be before the sys.path
from row import Row # noqa
from row import RowData # noqa
from rowset import RowSet # noqa

FILES_DIR = 'cash'

# Ensure we do not invent more money
decimal.getcontext().rounding = decimal.ROUND_DOWN


class HKT(datetime.tzinfo):

    def utcoffset(self, dt):
        return datetime.timedelta(hours=8)

    def tzname(self, dt):
        return "HKT"

    def dst(self, dt):
        return datetime.timedelta(hours=0)


def _iso8601_str(dt):
    """Why oh why is this so hard to do?
    """
    # now = datetime.datetime.now()
    # now_timestamp = now.timestamp()
    # utc_horror = datetime.datetime.utcfromtimestamp( now_timestamp ).timestamp() # noqa
    # delta_min = (now_timestamp - utc_horror) //60
    # sign="+"
    # if (delta_min<0):
    #     sign="-"
    #     delta_min = abs(delta_min)

    # delta_hr = delta_min /60.0
    # delta_min = (int(delta_hr) - delta_hr) * 60
    # delta_hr = int(delta_hr)
    # delta_str = sign+"{:02d}:{:02d}".format(delta_hr, delta_min)

    # Ideally, this would work, but the HKT class needs ... something ...
    # dt = dt.replace(tzinfo=HKT())
    # return dt.replace(microsecond=0).isoformat()

    dt = dt.replace(microsecond=0)
    dt = dt + datetime.timedelta(hours=8)
    timezone_str = "+08:00"
    return dt.strftime('%FT%T') + timezone_str


def render_month(date):
    """Return a short string representation of the date as a month
    """
    if isinstance(date, datetime.date):
        return date.strftime('%Y-%m')

    # Awkwardly, if we want to have a "Total" month or a "Average"
    # month, everything works except for this render_month function
    # TODO - fix this in a cleaner way
    return date


def render_month_len():
    """how much room to allow for each month column
    """
    # TODO - this should eventually move into some rendering code
    return 9


def forecast_filter(rows):
    """If the rowset has forecast data, try to guess if it also has
    superceeding real data, and if so, remove the forecast """

    if not rows.isforecast:
        return rows

    split = rows.group_by('isforecast')

    if len(split[True]) != 1:
        # there is more than one forecast item, dont filter
        return rows

    if False not in split:
        # There are no real items, dont filter
        return rows

    # it looks like we have good data to replace the forecast
    return split[False]


def grid_accumulate(rows):
    """Accumulate the rows into month+tag buckets
    """
    grid = {}
    totals = {}
    totals['total'] = RowSet()
    months_present = set()

    months = rows.group_by('month')
    for month in months:
        months_present.add(month)
        totals[month] = RowSet()

        tags = months[month].group_by('hashtag')

        for tag in tags:
            tagrows = forecast_filter(tags[tag])

            # I would prefer auto-vivification to all these if statements
            if tag not in grid:
                grid[tag] = {}

            grid[tag][month] = {}
            grid[tag][month]['sum'] = tagrows

            totals[month].append(tagrows)
            totals['total'].append(tagrows)

    running_totals = {}
    running_total = 0
    isforecast = False
    for month in sorted(months_present):
        if totals[month].isforecast:
            # Taint all future values
            isforecast = True

        running_total += totals[month].value

        # if we have only zeros after the decimal, change to an int
        if int(running_total) == running_total:
            running_total = running_total.to_integral_exact()

        if isforecast:
            running_totals[month] = '~' + str(running_total)
        else:
            running_totals[month] = running_total

    return months_present, grid, totals, running_totals


def grid_cell_tostr(cell):
    """Return the string version of a cell
    which might be a RowSet and might include forecast flags"""

    if isinstance(cell, (Row, RowSet)):
        if cell.isforecast:
            cell = '~' + str(cell.value)
        else:
            cell = cell.value
    return cell


def grid_render_onerow(prefix, prefix_len, rowdata, cell_len):
    s = []

    s += "{:<{width}}".format(prefix, width=prefix_len)

    for cell in rowdata:
        s += "{:>{}}".format(grid_cell_tostr(cell), cell_len)

    s += "\n"

    return s


def grid_render_colheader(months, months_len, tags_len):
    return grid_render_onerow(
        ' ', tags_len,
        [render_month(x) for x in months], months_len
    )


def grid_render_totals(months, totals, months_len, tags_len, running_totals):
    """
    months is a set of months (as datetime.date objects) that we want to render
    totals is a dictionary of the isolated month total
    months_len is the width needed to render one month column
    tags_len is the width needed to show the longest tag
    """
    s = []

    s += "\n"
    s += grid_render_onerow(
        'MONTH Sub Total', tags_len,
        [totals[x] for x in months], months_len
    )

    s += grid_render_onerow(
        'RUNNING Balance', tags_len,
        [running_totals[x] for x in months], months_len
    )

    s += "TOTAL: {:>{}}".format(grid_cell_tostr(totals['total']), months_len)

    return s


def grid_render_rows(months, tags, grid, months_len, tags_len):
    s = []

    tags = sorted(tags)

    # Output each tag on its own row
    for tag in tags:
        count_seen = 0
        cells = []
        for month in months:
            if month in grid[tag]:
                cells.append(grid[tag][month]['sum'])
                count_seen += 1
            else:
                cells.append('')

        # skip rendering for lines that have no cell data
        # (this could happen if the filter_hack is used)
        if count_seen == 0:
            continue

        s += grid_render_onerow(
            tag, tags_len,
            cells, months_len
        )

    return s


def grid_render(months, tags, grid, totals, running_totals):
    # Render the accumulated data

    tags_len = max([len(i) for i in tags])+1
    months_len = render_month_len()
    months = sorted(months)

    s = []
    s += grid_render_colheader(months, months_len, tags_len)
    s += grid_render_rows(months, tags, grid, months_len, tags_len)
    s += grid_render_totals(
        months, totals, months_len, tags_len, running_totals)

    return ''.join(s)


def topay_render(rows, strings):
    rows = rows.filter(['direction==outgoing'])
    alltags = sorted(rows.group_by('hashtag').keys())

    months = rows.group_by('month')

    s = []
    for month in sorted(months):
        s.append(strings['header'].format(date=render_month(month)))
        s.append("\n")
        s.append(strings['table_start'])
        s.append("\n")

        monthtags = months[month].group_by('hashtag')
        for hashtag in alltags:
            if hashtag in monthtags:
                price = monthtags[hashtag].value
                date = monthtags[hashtag].last().date
            else:
                price = "$0"
                date = "Not Yet"

            s.append(strings['table_row'].format(hashtag=hashtag.capitalize(),
                                                 price=price, date=date))
            s.append("\n")
        s.append(strings['table_end'])
        s.append("\n")

    return ''.join(s)


#
# This section contains the implementation of the commandline
# sub-commands.  Ideally, they are all small and simple, implemented with
# calls to the above functions.  This should allow clearer understanding
# of the intent of each sub-command
#


def subp_sum(args):
    result = args.rows.value
    # Only check the result for validity here and not in the class as
    # the RowSet could be storing a virtual account in other places
    if result < 0:
        raise ValueError(
            "Impossible negative value cash balance: {}".format(result))
    return "{}".format(result)


def subp_topay(args):
    strings = {
        'header': 'Date: {date}',
        'table_start': "Bill\t\t\tPrice\tPay Date",
        'table_end': '',
        'table_row': "{hashtag:<23}\t{price}\t{date}",
    }
    return topay_render(args.rows, strings)


def subp_topay_html(args):
    strings = {
        'header': '<h2>Date: <i>{date}</i></h2>',
        'table_start':
            "<table>\n" +
            "<tr><th>Bills</th><th>Price</th><th>Pay Date</th></tr>",
        'table_end': '</table>',
        'table_row': '''
    <tr>
        <td>{hashtag}</td><td>{price}</td><td>{date}</td>
    </tr>''',
    }
    return topay_render(args.rows, strings)


def subp_party(args):
    balance = args.rows.value
    return "Success" if balance > 0 else "Fail"


def subp_csv(args):

    # remove rows with no date (TODO: should csv output match input?)
    filtered = RowSet()
    filtered.append(list(filter(lambda d: d.date is not None, args.rows)))

    rows = RowSet()
    rows.append(sorted(filtered, key=lambda x: x.date))

    buf = StringIO()
    writer = csv.writer(buf)

    # Write header
    # TODO - this is the only user of the RowData in this file, remove it
    writer.writerow([row.capitalize() for row in RowData._fields])

    writer.writerows(rows)

    writer.writerow('')
    writer.writerow(('Sum',))
    writer.writerow((rows.value,))
    return buf.getvalue()


def subp_grid(args):
    # ensure that each category has a nice and clear prefix
    for row in args.rows:
        if row.hashtag is None:
            row.hashtag = 'unknown'

        # Most of the time, the in and out with either be
        # one-way or balance each other out to zero.  So,
        # we can avoid the extra lines to separate them.
        #
        # Occasionally, we might want to dig into the flow
        # to see where some strange number comes from
        if args.separate_inout:
            if row.direction == 'outgoing':
                row.hashtag = row.hashtag + ' out'
            else:
                row.hashtag = row.hashtag + ' in'

    (months, grid, totals, running_totals) = grid_accumulate(args.rows)

    # FIXME - tags contains entries that might be filtered
    tags = args.rows.group_by('hashtag').keys()

    if args.filter_hack:
        today = datetime.date.today()
        oldest = today - datetime.timedelta(args.filter_hack)

        months = [month for month in months if month > oldest]

    return grid_render(months, tags, grid, totals, running_totals)


def subp_json_payments(args):

    payments = args.rows.filter(['direction==incoming']).group_by('hashtag')

    r = {}
    for tag, payment in payments.items():
        r[tag] = render_month(payment.last().date)
    return json.dumps((r))


def subp_make_balance(args):
    # Load the template file
    # TODO - use a string or an arg for the template source
    with open(os.path.join(os.path.dirname(__file__),
                           './docs/template.html')) as f:
        tpl = f.read()

    # Filter out only the membership dues
    grid_rows = args.rows.filter([
        'hashtag=~^dues:',
        'rel_months>-5',
        'rel_months<1',
    ])

    # Make the category look pretty
    for row in grid_rows:
        a = row.hashtag.split(':')
        row.hashtag = ''.join(a[1:]).title()

    (months, grid, totals, running_totals) = grid_accumulate(grid_rows)
    tags = grid_rows.group_by('hashtag').keys()
    months = sorted(months)

    months_len = render_month_len()
    tags_len = max([len(i) for i in tags])+1

    header = ''.join(grid_render_colheader(months, months_len, tags_len))
    grid = ''.join(grid_render_rows(months, tags, grid, months_len, tags_len))

    def _get_next_rent_month():
        last_payment = args.rows.group_by('hashtag')['bills:rent'].last()
        date = last_payment.date

        # The landlord states that "the monthly rental payment should
        # be settled seven (7) days in advance prior to the 1st day of
        # each and every rental month"
        #
        # Implement business logic to find this date
        #
        # assuming the rent transactions have been placed into the
        # month that they are paying the rent for, we can find the date
        # that the rent is next due by clamping the day to seven days
        # before the end of the month

        # set to the due date during at the end of the month
        date = date.replace(
            day=calendar.monthrange(date.year, date.month)[1] - 7
        )

        return date

    def _get_hashtag_value(tag):
        """Return the value for a hashtag group, or a zero"""
        # FIXME - should cache the group_by
        hashtags = args.rows.group_by('hashtag')

        if tag in hashtags:
            return hashtags[tag].value
        else:
            return 0

    def _get_balance_this_month():
        month_data = args.rows.filter([
            'rel_months==0',
        ])

        return month_data.value

    macros = {
        'balance_this_month': _get_balance_this_month(),
        'balance_sum': args.rows.value,
        'grid_header': header,
        'grid':        grid,
        'rent_due':    _get_next_rent_month(),
        'time_now':    _iso8601_str(datetime.datetime.utcnow()),
        'loan':        _get_hashtag_value('loan')
    }
    return string.Template(tpl).substitute(macros)


def subp_roundtrip(args):
    """Allow round-tripping the input data"""
    return str(args.rows)


def create_stats(args):
    # stats are only likely to be valid for previous months
    rows = args.rows.filter(['rel_months<0'])
    current_month = args.rows.filter(['rel_months==0'])

    def make_rowset(value):
        fake = Row()
        fake.value = value
        r = RowSet()
        r.append(fake)
        return r

    def stats_rowset(rowset):
        r = {}
        r['incoming'] = rowset.filter(['value>0'])
        r['outgoing'] = rowset.filter(['value<0'])
        # TODO - values of zero?  we have one member as such, but it is a
        # exceptional case
        r['dues'] = rowset.filter(['hashtag=~^dues:'])
        r['members'] = len(r['dues'].group_by('hashtag').keys())
        if r['members']:
            r['ARPM'] = int(r['dues'].value / r['members'])
        else:
            r['ARPM'] = -1

        r['other'] = rowset.filter(['value>0', 'hashtag!~^dues:'])

        return r

    result = {}
    months = rows.group_by('month')
    for k, month in months.items():
        result[k] = stats_rowset(month)

    months = sorted(result.keys())

    result['Total'] = stats_rowset(rows)

    result['Average'] = {}
    for tag in ('outgoing', 'incoming', 'dues', 'other'):
        result['Average'][tag] = make_rowset(
            result['Total'][tag].value / len(months))
    result['Average']['members'] = int(sum(
        [result[x]['members'] for x in months]
    ) / len(months))
    result['Average']['ARPM'] = int(
        result['Total']['dues'].value /
        result['Average']['members'] /
        len(months)
    )

    result['MonthTD'] = stats_rowset(current_month)

    months.append('Average')
    months.append('MonthTD')
    months.append('Total')

    balance = 0
    for month in months:
        result[month]['subtotal'] = (
            result[month]['incoming'].value
            + result[month]['outgoing'].value
        )
        balance += result[month]['subtotal']
        result[month]['balance'] = balance

    return result, months


def subp_statstsv(args):
    result, months = create_stats(args)

    fields = (
        'balance',
        'subtotal',
        'outgoing',
        'incoming',
        'dues',
        'other',
        'members',
        'ARPM',
    )
    s = []

    s += "#column 1 timestamp\n"
    column_nr = 3
    for field in fields:
        s += '#column '
        s += str(column_nr)
        s += ' '
        s += field
        column_nr += 1
        s += "\n"

    for month in months:
        if isinstance(month, str):
            # its one of our rollup fake months
            if month == 'MonthTD':
                # Use the path via datetime now() so that we can use the
                # existing mock in the test suite
                thismonth = datetime.datetime.now().date().replace(day=1)
                s += thismonth.strftime('%s')
            else:
                s += "# x"
        else:
            s += month.strftime('%s')

        s += ' '
        s += render_month(month)
        s += ' '

        # TODO
        # - the timestamp is for the 1st of the month, however
        #   all the stats are "as of end of month" - thus the
        #   timestamp should probably be incremented to make
        #   clear to anyone spelunking in the stats

        for field in fields:
            val = result[month][field]
            if isinstance(val, (int, decimal.Decimal)):
                s += str(val)
            else:
                s += str(result[month][field].value)
            s += ' '

        s += "\n"
    return ''.join(s)


def subp_stats(args):
    result, months = create_stats(args)

    months_len = render_month_len()+2
    tags_len = 13

    s = []
    s += grid_render_colheader(months, months_len, tags_len)
    for tag in ('outgoing', 'incoming'):
        s += grid_render_onerow(
            tag, tags_len,
            [result[x][tag].value.to_integral_exact(
                rounding=decimal.ROUND_FLOOR
            ) for x in months],
            months_len
        )
    s += "\n"
    for tag in ('dues', 'other'):
        s += grid_render_onerow(
            " {}:".format(tag), tags_len,
            [result[x][tag].value.to_integral_exact(
                rounding=decimal.ROUND_FLOOR
            ) for x in months],
            months_len
        )
    s += "\n"
    s += grid_render_onerow(
        'nr members', tags_len,
        [result[x]['members'] for x in months],
        months_len
    )
    s += grid_render_onerow(
        'ARPM', tags_len,
        [result[x]['ARPM'] for x in months],
        months_len
    )

    # The rows after this are identical in the Average and Total columns,
    # so to make that easier to see, remove the Total column from display
    # Also remove the MonthTD, since the calcualted numbers will be bogus
    # until near the end of the month
    months = months[:-2]

    def members_given_dues_outgoing(dues, rowset):
        months = len(rowset.group_by('month').keys())
        if months == 0:
            # TODO HACK
            # - since we use a 'magic' column name that is not a date, there
            # is at least one column that doesnt group_by properly
            months = 1
        total_dues = dues * months
        return abs((rowset.value / total_dues).to_integral_exact(
            rounding=decimal.ROUND_FLOOR
        ))

    def dues_given_members_outgoing(members, rowset):
        if members == 0:
            # no value possible!
            return 0

        months = len(rowset.group_by('month').keys())
        if months == 0:
            # TODO HACK
            # - since we use a 'magic' column name that is not a date, there
            # is at least one column that doesnt group_by properly
            months = 1
        return abs(rowset.value / members / months).to_integral_exact(
            rounding=decimal.ROUND_FLOOR
        )

    s += "\n"
    s += "members needed\n"

    # Which fee rates do we want to see membership numbers for?
    # Add in the recent official numbers
    fees_rates = set([500, 700])
    # Also add in some of the average revenue numbers
    fees_rates.add(result['Average']['ARPM'])
    fees_rates.add(result['MonthTD']['ARPM'])
    for dues in sorted(fees_rates):
        s += grid_render_onerow(
            " dues {}".format(dues), tags_len,
            [members_given_dues_outgoing(dues, result[x]['outgoing'])
             for x in months],
            months_len
        )

    s += "dues needed\n"

    # Which membership numbers do we want to see needed fees for?
    members_count = set([17, 30])
    # add in some of the average member numbers
    members_count.add(result['Average']['members'])
    members_count.add(result['MonthTD']['members'])

    for members in sorted(members_count):
        s += grid_render_onerow(
            " members {}".format(members), tags_len,
            [dues_given_members_outgoing(members, result[x]['outgoing'])
             for x in months],
            months_len
        )

    s += "\nNote: Total column does not include MonthTD numbers\n"

    return ''.join(s)


def subp_check_doubletxn(args):
    """
    Go through each transaction in a month.  Alert if there are two
    or more transactions that have the same dollar amount and the
    same tag
    """

    rows = args.rows.filter([
        'hashtag=~^dues:',
    ])

    # TODO:
    # - get better at deduplicating the non dues payments too?

    db = {}
    for row in rows:
        month = row.month
        if month not in db:
            db[month] = {}

        tag = row.hashtag
        if tag is None:
            # TODO - ensure that every line has a tag?
            continue

        if tag not in db[month]:
            db[month][tag] = {}

        value = row.value
        if value in db[month][tag]:
            raise ValueError(
                "Duplicate transaction found:\n{}\n{}".format(
                    db[month][tag][value],
                    row))

        db[month][tag][value] = row


def subp_report_location(args):
    """
    Report on the balance of each location found in the dataset.  For best
    results, the indivdual transactions should have a "location" bangtag.
    During a transition period, a comment pattern heuristic is applied.

    TODO:
    - consolidate the bank and bank_deduct groups
    - Update the old transactions to use the location bangtag and remove
      the heuristics
    """

    split = RowSet()
    for row in args.rows:
        split.append(row._split_locn_xfer())
    args.rows = split

    for row in args.rows:
        if row.comment is None:
            continue
        if re.search(r'cash on bank', row.comment, re.IGNORECASE):
            row._set_bangtag('locn', ['bank_nic'])
        if re.search(r'deducted from bank', row.comment, re.IGNORECASE):
            row._set_bangtag('locn', ['bank_nic_deduct'])
        if re.search(r'cash on paypal', row.comment, re.IGNORECASE):
            row._set_bangtag('locn', ['paypal'])

    groups = args.rows.group_by('location')

    s = []

    if args.verbose > 0:
        for locn in sorted(groups.keys()):
            s += locn
            s += ':\n'
            s += str(groups[locn])
            s += '\n'

        s += '\n\n'

    s += '\nTOTALS\n\n'
    for locn in sorted(groups.keys()):
        s += locn
        s += ' '
        s += str(groups[locn].value)
        s += '\n'

    return ''.join(s)


# A list of all the sub-commands
subp_cmds = {
    'check_doubletxn': {
        'func': subp_check_doubletxn,
        'help': 'Check for identical transactions in each month',
    },
    'csv': {
        'func': subp_csv,
        'help': 'Output transactions as csv',
    },
    'grid': {
        'func': subp_grid,
        'help': 'Output a grid of transaction tags vs months',
    },
    'json_payments': {
        'func': subp_json_payments,
        'help': 'Output JSON of incoming payments',
    },
    'make_balance': {
        'func': subp_make_balance,
        'help': 'Output sum HTML page',
    },
    'party': {
        'func': subp_party,
        'help': 'Is it party time or not?',
    },
    'roundtrip': {
        'func': subp_roundtrip,
        'help': 'Output the database the same way as the input',
    },
    'sum': {
        'func': subp_sum,
        'help': 'Sum all transactions',
    },
    'topay': {
        'func': subp_topay,
        'help': 'List all pending payments',
    },
    'topay_html': {
        'func': subp_topay_html,
        'help': 'List all pending payments as HTML table',
    },
    'stats': {
        'func': subp_stats,
        'help': 'Output finance stats report',
    },
    'statstsv': {
        'func': subp_statstsv,
        'help': 'Output finance stats report as TSV',
    },
    'report_location': {
        'func': subp_report_location,
        'help': 'Show where the cash is, using the location metadata',
    },
}

#
# Most of this is boilerplate and stays the same even with addition of
# features.  The only exception is if a sub-command needs to add a new
# commandline option.
#
if __name__ == '__main__':  # pragma: no cover
    argparser = argparse.ArgumentParser(
        description='Run calculations and transformations on cash data')
    argparser.add_argument('-v', '--verbose', action='count', default=0)
    argparser.add_argument('--dir',
                           action='store',
                           type=str,
                           default=os.path.join(os.path.join(
                               os.path.dirname(__file__), FILES_DIR)),
                           help='Input directory')
    argparser.add_argument('--includefuture',
                           action='store_true',
                           help='Include predicted future transactions from '
                           'a separate input directory')
    argparser.add_argument('--filter', action='append',
                           help='Add a key=value filter to the rows used')
    argparser.add_argument('--split', dest='split',
                           action='store_true',
                           help='Split rows that cover multiple months')
    argparser.add_argument('--nosplit', dest='split',
                           action='store_false',
                           help='Do not split rows that cover multiple months')
    argparser.set_defaults(split=True)

    subp = argparser.add_subparsers(help='Subcommand', dest='cmd')
    subp.required = True
    for key, value in subp_cmds.items():
        value['parser'] = subp.add_parser(key, help=value['help'])
        value['parser'].set_defaults(func=value['func'])

    # Add an additional commandline option for the "grid" subcommand
    subp_cmds['grid']['parser'].add_argument('--separate_inout',        # noqa
        action='store_const', const=True, default=False,                # noqa
        help='Show incoming and outgoing on separate lines of the grid' # noqa
    )                                                                   # noqa
    subp_cmds['grid']['parser'].add_argument('--filter_hack',        # noqa
        type=int,                                # noqa
        help='Quick hack specifying oldest entries to display - the arg is the number of days' # noqa
    )                                                                   # noqa
    subp_cmds['grid']['parser'].set_defaults(filter_hack=640)
    #
    # Hello? is that flake8?  I'd like to talk to you about presentation
    # values.  I know you like to keep lines under 78 characters wide, and
    # I support you in this goal, as it mostly makes sense.  I also know that
    # you like the idea of indenting options to the same depth as the opening
    # parenthesis, which sometimes makes sense too.
    # However, if that opening parenthesis is 45 chars deep, and one of the
    # parameters is a verbose help text line, you are just full of shit and
    # should be shot if you think the results look sane.
    #
    # So I shot you.
    #
    # Everybody is sad.  You are sad because I'm not meeting your ugly style
    # guide, I am sad because all my nicely readable lines now have this ugly
    # comment at the end of them.
    #
    # Now get off my lawn

    args = argparser.parse_args()

    if not os.path.exists(args.dir):
        raise RuntimeError('Directory "{}" does not exist'.format(args.dir))

    # first, load the main data
    args.rows = RowSet()
    args.rows.load_directory(args.dir)

    # next, optionally load additional directories
    # TODO - make these loaders into a generic list of directories
    if args.includefuture:
        args.rows.load_directory(
            os.path.join(args.dir, "future"),
            skip_balance_check=True
        )

    # optionally split multi-month transactions into one per month
    if args.split:
        args.rows = args.rows.autosplit()

    # apply any filters requested
    args.rows = args.rows.filter(args.filter)

    result = args.func(args)
    print(result)
