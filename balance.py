#!/usr/bin/env python
# Licensed under GPLv3
from collections import namedtuple
from datetime import datetime
from decimal import Decimal
import argparse
import os.path
import sys
import csv
import os
import re


FILES_DIR = 'cash'
IGNORE_FILES = ('membershipfees',)
HASHTAGS = ('rent', 'electricity', 'internet', 'water')


def list_files(dirname):
    for f in os.listdir(dirname):
        if f not in IGNORE_FILES:
            yield f


class Row(namedtuple('Row', ('value', 'date', 'comment', 'direction'))):

    def __new__(cls, value, date, comment, direction):
        value = Decimal(value)
        date = datetime.strptime(date.strip(), "%Y-%m-%d")

        if direction not in ('incoming', 'outgoing'):
            raise ValueError('Direction "{}" unhandled'.format(direction))

        # Inverse value
        if direction == 'outgoing':
            value = Decimal(0)-value

        obj = super(cls, Row).__new__(cls, value, date, comment, direction)
        return obj

    def __add__(self, value):
        if isinstance(value, Row):
            value = value.value

        return self.value + value

    def __radd__(self, value):
        return self.__add__(value)

    def month(self):
        return self.date.strftime('%Y-%m')

    def hashtag(self):
        """Look at the comment for this row and extract any hashtags found
        """
        p = re.compile('#(\S+)')
        all_tags = p.findall(self.comment)

        # TODO - have a better plan for what to do with multiple tags
        if len(all_tags) > 1:
            raise ValueError('Row has multiple tags: {}'.format(all_tags))

        if len(all_tags) == 0:
            return None

        return all_tags[0]

    def match(self, **kwargs):
        """using kwargs, check if this Row matches if so, return it, or None
        """

        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise AttributeError('Object has no attr "{}"'.format(key))
            attr = getattr(self, key)
            if callable(attr):
                attr = attr()
            if value != attr:
                return None

        return self


def find_hashtag(keyword, rows):
    '''Find a hash tag in the payment history'''
    matching = [x for x in rows if x.match(hashtag=keyword)]

    if not matching:
        return (False, '$0', 'Not yet')

    # TODO - Accumulate the data:  sum the value, max the date ?
    if len(matching) > 1:
        raise ValueError(
            'Multiple rows found with same hashtag: {}'.format(keyword))

    return (True, -matching[0].value, matching[0].date)


def parse_dir(dirname):
    '''Take all files in dirname and return Row instances'''

    for filename in list_files(dirname):
        direction, _ = filename.split('-', 1)

        with open(os.path.join(dirname, filename), 'r') as tsvfile:
            reader = csv.reader(tsvfile, delimiter='\t')

            for row in reader:
                yield Row(*row, direction=direction)


def filter_outgoing_payments(rows, month):
    '''Filter the given rows list for outgoing payments in the given month'''
    ret = [
        row for row in rows
        if row.match(month=month, direction='outgoing')
    ]
    ret.sort(key=lambda x: x.date)
    return ret


def get_payment_months(rows):
    months = set()
    for row in rows:
        months.add(row.month())
    ret = list(months)
    ret.sort()
    return ret


def topay_render(all_rows, strings):
    s = []
    for date in get_payment_months(all_rows):
        s.append(strings['header'].format(date=date))
        s.append("\n")
        rows = filter_outgoing_payments(all_rows, date)
        s.append(strings['table_start'])
        s.append("\n")
        for hashtag in HASHTAGS:
            paid, price, date = find_hashtag(hashtag, rows)
            s.append(strings['table_row'].format(hashtag=hashtag.capitalize(),
                                                 price=price, date=date))
            s.append("\n")
        s.append(strings['table_end'])
        s.append("\n")

    return ''.join(s)


def grid_render(rows):
    """Accumulate the rows into month+tag buckets, then render this as text
    """
    months = set()
    tags = set()
    grid = {}
    totals = {}
    totals['total'] = 0

    # Accumulate the data
    for row in rows:
        month = row.month()
        tag = row.hashtag()

        if tag is None:
            tag = 'unknown'

        if row.direction == 'outgoing':
            tag = 'out ' + tag
        else:
            tag = 'in ' + tag

        tag = tag.capitalize()

        # I would prefer auto-vivification to all these if statements
        if tag not in grid:
            grid[tag] = {}
        if month not in grid[tag]:
            grid[tag][month] = 0
        if month not in totals:
            totals[month] = 0

        # sum this row into various buckets
        grid[tag][month] += row.value
        totals[month] += row.value
        totals['total'] += row.value
        months.add(month)
        tags.add(tag)

    # Technically, this function could be split here into an accumulate
    # and a render function, but until there is a second consumer, that
    # is just a complication

    # Render the accumulated data
    s = []

    tags_len = max([len(tag) for tag in tags])
    months = sorted(months)

    # Skip the column of tag names
    s.append(' '*tags_len)
    s.append("\t")

    # Output the month row headings
    for month in months:
        s.append(month)
        s.append("\t")

    s.append("\n")

    # Output each tag
    for tag in sorted(tags):
        s.append("{:<{width}}\t".format(tag, width=tags_len))

        for month in months:
            if month in grid[tag]:
                s.append("{:>7}\t".format(grid[tag][month]))
            else:
                s.append("\t")

        s.append("\n")

    s.append("\n")
    s.append("{:<{width}}\t".format('TOTALS', width=tags_len))

    for month in months:
        s.append("{:>7}\t".format(totals[month]))

    s.append("\n")
    s.append("TOTAL:\t{:>7}".format(totals['total']))

    return ''.join(s)

#
# This section contains the implementation of the commandline
# sub-commands.  Ideally, they are all small and simple, implemented with
# calls to the above functions.  This will allow the simple unit tests
# to provide confidence that none of the above functions are broken,
# without needing the sub-commands to be tested (which would need a
# more complex test system)
#


def subp_sum(args):
    print("{}".format(sum(parse_dir(args.dir))))


def subp_topay(args):
    strings = {
        'header': 'Date: {date}',
        'table_start': "Bill\t\tPrice\tPay Date",
        'table_end': '',
        'table_row': "{hashtag:<15}\t{price}\t{date}",
    }
    all_rows = list(parse_dir(args.dir))
    print(topay_render(all_rows, strings))


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
    all_rows = list(parse_dir(args.dir))
    print(topay_render(all_rows, strings))


def subp_party(args):
    balance = sum(parse_dir(args.dir))
    print("Success" if balance > 0 else "Fail")


def subp_csv(args):
    rows = sorted(parse_dir(args.dir), key=lambda x: x.date)

    with (open(args.csv_out, 'w') if args.csv_out else sys.stdout) as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow([row.capitalize() for row in Row._fields])

        for row in rows:
            writer.writerow(row)

        writer.writerow('')
        writer.writerow(('Sum',))
        writer.writerow((sum(rows),))


def subp_grid(args):
    rows = list(parse_dir(args.dir))
    print(grid_render(rows))


# A list of all the sub-commands
subp_cmds = {
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
    'party': {
        'func': subp_party,
        'help': 'Is it party time or not?',
    },
    'csv': {
        'func': subp_csv,
        'help': 'Output transactions as csv',
    },
    'grid': {
        'func': subp_grid,
        'help': 'Output a grid of transaction tags vs months',
    },
}

#
# Most of this is boilerplate and stays the same even with addition of
# features.  The only exception is if a sub-command needs to add a new
# commandline option.
#
if __name__ == '__main__':
    argparser = argparse.ArgumentParser(
        description='Run calculations and transformations on cash data')
    argparser.add_argument('--dir',
                           action='store',
                           type=str,
                           default=FILES_DIR,
                           help='Input directory')
    subp = argparser.add_subparsers(help='Subcommand', dest='cmd')
    subp.required = True
    for key, value in subp_cmds.items():
        value['parser'] = subp.add_parser(key, help=value['help'])

    # Add a new commandline option for the "csv" subcommand
    subp_cmds['csv']['parser'].add_argument('--out',
                                            action='store',
                                            type=str,
                                            default=None,
                                            dest='csv_out',
                                            help='Output file')

    args = argparser.parse_args()

    if not os.path.exists(args.dir):
        raise RuntimeError('Directory "{}" does not exist'.format(args.dir))

    if args.cmd in subp_cmds:
        subp_cmds[args.cmd]['func'](args)

    else:
        raise ValueError('Unknown command "{}"'.format(args.cmd))
