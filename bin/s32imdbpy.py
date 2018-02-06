#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s32imdbpy.py script.

This script imports the s3 dataset distributed by IMDb into a SQL database.

Copyright 2017 Davide Alberani <da@erlug.linux.it>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""

import os
import glob
import gzip
import logging
import argparse
import sqlalchemy

from imdb.parser.s3.utils import DB_TRANSFORM

TSV_EXT = '.tsv.gz'
# how many entries to write to the database at a time.
BLOCK_SIZE = 10000

logger = logging.getLogger()
logger.setLevel(logging.INFO)
metadata = sqlalchemy.MetaData()


def generate_content(fd, headers, table):
    """Generate blocks of rows to be written to the database.

    :param fd: a file descriptor for the .tsv.gz file
    :type fd: :class:`_io.TextIOWrapper`
    :param headers: headers in the file
    :type headers: list
    :param table: the table that will populated
    :type table: :class:`sqlalchemy.Table`
    :returns: block of data to insert
    :rtype: list
    """
    data = []
    headers_len = len(headers)
    data_transf = {}
    for column, conf in DB_TRANSFORM.get(table.name, {}).items():
        if 'transform' in conf:
            data_transf[column] = conf['transform']
    for line in fd:
        s_line = line.decode('utf-8').strip().split('\t')
        if len(s_line) != headers_len:
            continue
        info = dict(zip(headers, [x if x != r'\N' else None for x in s_line]))
        for key, tranf in data_transf.items():
            if key not in info:
                continue
            info[key] = tranf(info[key])
        data.append(info)
        if len(data) >= BLOCK_SIZE:
            yield data
            data = []
    if data:
        yield data
        data = []


def build_table(fn, headers):
    """Build a Table object from a .tsv.gz file.

    :param fn: the .tsv.gz file
    :type fn: str
    :param headers: headers in the file
    :type headers: list
    """
    logging.debug('building table for file %s' % fn)
    table_name = fn.replace(TSV_EXT, '').replace('.', '_')
    table_map = DB_TRANSFORM.get(table_name) or {}
    columns = []
    for header in headers:
        col_info = table_map.get(header) or {}
        col_type = col_info.get('type') or sqlalchemy.UnicodeText
        col_obj = sqlalchemy.Column(header, col_type)
        columns.append(col_obj)
    return sqlalchemy.Table(table_name, metadata, *columns)


def import_file(fn, engine):
    """Import data from a .tsv.gz file.

    :param fn: the .tsv.gz file
    :type fn: str
    :param engine: SQLAlchemy engine
    :type engine: :class:`sqlalchemy.engine.base.Engine`
    """
    logging.info('begin processing file %s' % fn)
    connection = engine.connect()
    count = 0
    with gzip.GzipFile(fn, 'r') as gz_file:
        headers = gz_file.readline().decode('utf-8').strip().split('\t')
        table = build_table(os.path.basename(fn), headers)
        insert = table.insert()
        metadata.create_all(tables=[table])
        block = []
        try:
            for block in generate_content(gz_file, headers, table):
                connection.execute(insert, block)
                count += len(block)
        except Exception as e:
            logging.error('error processing data: %d entries lost: %s' % (len(block), e))
        logging.info('end processing file %s: %d entries' % (fn, count))


def import_dir(dir_name, engine):
    """Import data from a series of .tsv.gz files.

    :param dir_name: directory containing the .tsv.gz files
    :type dir_name: str
    :param engine: SQLAlchemy engine
    :type engine: :class:`sqlalchemy.engine.base.Engine`
    """
    for fn in glob.glob(os.path.join(dir_name, '*%s' % TSV_EXT)):
        if not os.path.isfile(fn):
            logging.debug('skipping file %s' % fn)
            continue
        import_file(fn, engine)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('tsv_files_dir')
    parser.add_argument('db_uri')
    parser.add_argument('--verbose', help='increase verbosity', action='store_true')
    args = parser.parse_args()
    dir_name = args.tsv_files_dir
    db_uri = args.db_uri
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    engine = sqlalchemy.create_engine(db_uri, echo=False)
    metadata.bind = engine
    import_dir(dir_name, engine)

