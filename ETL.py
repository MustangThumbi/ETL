import csv
import re
# this module helps in processing XML files.
import xml.etree.ElementTree as ET
from datetime import datetime

# Regular expression to detect potential string values with missing quotes
sql_fun = ['true', 'false', 'avg', 'count', 'first', 'last', 'max', 'min',
           'sum', 'ucase', 'lcase', 'mid', 'len', 'round', 'now', 'format']
string_exp = re.compile('^(?!["\']|{}).*[a-z]'.format('|'.join(sql_fun)),
                        re.IGNORECASE)


class CsvImporter(object):
    def __init__(self, path, dialect, import_specs):
        self.path = path
        self.dialect = dialect
        # Flatten import_specs to {(table, instance): record_spec} "t,i,s" form
        flat_specs = {}
        for (t, table_spec) in import_specs.items():
            specs = {(t, i): s for (i, s) in table_spec.items()}
            flat_specs.update(specs)
        # Create a XReference dependency map and sort it topologically
        dependency_map = {}
        for (path, s) in flat_specs.items():
            deps = set([(x.table_name, x.instance_name) for x
                        in s.attr_map.values() if isinstance(x, XReference)])
            dependency_map[path] = deps
        sorted_keys = [val for sub in _toposort(dependency_map) for val in sub]
        # Store sorted results in a list [(t, i, s), ...]
        self.specs = [(k[0], k[1], flat_specs[k]) for k in sorted_keys]

    def import_data(self, id_col=None):
        records = []
        with open(self.path) as f:
            csv.register_dialect('csv2db', **self.dialect)
            reader = csv.DictReader(f, dialect='csv2db')
            row_num = 0
            for row in reader:
                row_id = row[id_col] if id_col else row_num
                records += self._records_for_row(row, row_id)
                row_num += 1
        return records

    def _records_for_row(self, row, row_id):
        records = []
        xref_map = {}
        for (table, instance, spec) in self.specs:
            if spec.condition(row) is False:
                continue
            # Create record and import attributes according to spec
            record = DbRecord(table, row_id)
            record.import_attributes(spec.attr_map, xref_map, row)
            records.append(record)
            # Keep a reference to each record instance that we create for
            # resolving XReferences in later instances
            instance_path = (table, instance)
            xref_map[instance_path] = record

        return records


class RecordSpec(object):
    def __init__(self, attr_map, condition=None):
        self.attr_map = attr_map
        self.condition = condition if condition else lambda row: True


class ColumnValue(object):
    def __init__(self, col_name, convert=None):
        self.col_name = col_name
        self.convert = convert

    def _read(self, row, **kw_args):
        value = row[self.col_name]
        return self.convert(value) if self.convert else value


class MultiColumnValue(object):
    def __init__(self, col_names, convert):
        if not convert:
            raise ValueError('ERROR: You must provide a convert function')
        self.col_names = col_names
        self.convert = convert

    def _read(self, row, **kw_args):
        values = {key: row[key] for key in self.col_names}
        return self.convert(values)


class ConstValue(object):
    def __init__(self, value):
        self.value = value

    def _read(self, row, **kw_args):
        return self.value


class DynamicValue(object):
    def __init__(self, generate):
        self.generate = generate

    def _read(self, row, **kw_args):
        return self.generate(row)


class XReference(object):
    def __init__(self, table_name, instance_name, attribute_name):
        self.table_name = table_name
        self.instance_name = instance_name
        self.attribute_name = attribute_name

    def _read(self, row, **kw_args):
        existing_records = kw_args['existing_records']
        path = (self.table_name, self.instance_name)
        value = existing_records[path].attributes[self.attribute_name]
        return value


class DbRecord(object):
    def __init__(self, table_name, row_id):
        self.row_id = row_id
        self.table_name = table_name
        self.attributes = {}

    def import_attributes(self, attr_map, existing_records, row):
        try:
            imported = {k: v._read(row, existing_records=existing_records)
                        for (k, v) in attr_map.iteritems()}
        except AttributeError:
            k, v = next((k, v) for (k, v) in attr_map.iteritems()
                        if '_read' not in dir(v))
            print('ERROR: The RecordSpec for {} in {} does not seem to be '
                  'valid'.format(k, self.table_name))
            exit(-1)
        self.attributes.update(imported)

    def insert_statement(self):
        col = ' (%s)' % ', '.join(self.attributes.keys())
        # sanity checks
        error = False
        for k, v in self.attributes.iteritems():
            if not isinstance(v, str):
                print('ERROR: The value ({}) for "{}" in table "{}" is not a '
                      'string. Make sure your specs only produce string '
                      'values (i.e. \'5\', \'TRUE\', \'"Some text"\', '
                      '...)'.format(v, k, self.table_name))
                error = True
            elif string_exp.match(v):
                print('WARNING: {} looks like a string value but is not in '
                      'quotes. If "{}" in "{}" is a CHAR or VARCHAR type '
                      'column, you should put the value in quotes.').\
                    format(v, k, self.table_name)
        if error:
            print('Aborting due to errors.')
            exit(-1)
        val = ' (%s)' % ', '.join(self.attributes.values())
        sql = 'INSERT INTO ' + self.table_name + col + ' VALUES' + val + ';\n'
        return sql
# Private (internal) methods


def _toposort(data):
    # Ignore self dependencies.
    for k, v in data.items():
        v.discard(k)
    # Find all items that don't depend on anything
    extra_items_in_deps = reduce(
        set.union, data.itervalues()) - set(data.iterkeys())

    # Add empty dependences where needed
    data.update({item: set() for item in extra_items_in_deps})

    while True:
        ordered = set(item for item, dep in data.iteritems() if not dep)
        if not ordered:
            break
        yield ordered
        data = {item: (dep - ordered)
                for item, dep in data.iteritems() if item not in ordered}
    assert not data, "Cyclic dependencies:\n%s" % \
        '\n'.join(repr(x) for x in data.iteritems())
