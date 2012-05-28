#!/usr/bin/env python

# BEGIN_COPYRIGHT
# 
# Copyright 2012 CRS4.
# 
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
# 
#   http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
# 
# END_COPYRIGHT

import argparse, os, random, sys, tempfile, warnings

import pydoop
import pydoop.hdfs
import pydoop.hadut as hadut


PIPES_TEMPLATE = """
import sys
import os
sys.path.insert(0, os.getcwd())

import pydoop.pipes
import %(module)s

class ContextWriter(object):
  def __init__(self, context):
          self.context = context
          self.counters = dict()

  def emit(self, k, v):
          self.context.emit(str(k), str(v))

  def count(self, what, howmany):
          if self.counters.has_key(what):
                  counter = self.counters[what]
          else:
                  counter = self.context.getCounter('%(module)s', what)
                  self.counters[what] = counter
          self.context.incrementCounter(counter, howmany)

  def status(self, msg):
          self.context.setStatus(msg)

  def progress(self):
          self.context.progress()

class PydoopScriptMapper(pydoop.pipes.Mapper):
  def __init__(self, ctx):
          super(type(self), self).__init__(ctx)
          self.writer = ContextWriter(ctx)

  def map(self, ctx):
          %(module)s.%(map_fn)s(ctx.getInputKey(), ctx.getInputValue(), self.writer)

class PydoopScriptReducer(pydoop.pipes.Reducer):
  def __init__(self, ctx):
          super(type(self), self).__init__(ctx)
          self.writer = ContextWriter(ctx)

  @staticmethod
  def iter(ctx):
          while ctx.nextValue():
                  yield ctx.getInputValue()

  def reduce(self, ctx):
          key = ctx.getInputKey()
          %(module)s.%(reduce_fn)s(key, PydoopScriptReducer.iter(ctx), self.writer)

## main
result = pydoop.pipes.runTask( pydoop.pipes.Factory(PydoopScriptMapper, PydoopScriptReducer) )
sys.exit(0 if result else 1)
"""


class PydoopScript(object):

  DefaultReduceTasksPerNode = 3

  class Args(argparse.Namespace):
    def __init__(self):
      self.properties = {}

  class SetProperty(argparse.Action):
    """
    Used with argparse to parse arguments setting property values.
    Creates an attribute 'property' in the results namespace containing
    all the property-value pairs read from the command line.
    """
    def __call__(self, parser, namespace, value, option_string=None):
      name, v = value.split('=', 1)
      namespace.properties[name] = v

  def __init__(self):
    self.parser = argparse.ArgumentParser(
      description="Easy MapReduce scripting with Pydoop"
      )
    self.parser.add_argument(
      'module', metavar='MODULE', help='Python module file'
      )
    self.parser.add_argument(
      'input', metavar='INPUT', help='hdfs input path'
      )
    self.parser.add_argument(
      'output', metavar='OUTPUT', help='hdfs output path'
      )
    self.parser.add_argument(
      '-m', '--map-fn', metavar='MAP', default='mapper',
      help="Name of map function within module (default: mapper)"
      )
    self.parser.add_argument(
      '-r', '--reduce-fn', metavar='RED', default='reducer',
      help="Name of reduce function within module (default: reducer)"
      )
    self.parser.add_argument(
      '-t', '--kv-separator', metavar='SEP', default='\t',
      help="Key-value separator in final output (default: tab character)"
      )
    self.parser.add_argument(
      '--num-reducers', metavar='INT', type=int,
      help="Number of reduce tasks. Specify 0 to only perform map phase " +
      "(default: 3 * num task trackers)"
      )
    self.parser.add_argument(
      '--no-override-home', action='store_true',
      help="Don't set the script's HOME directory to the $HOME in your " +
      "environment.  Hadoop will set it to the value of the " +
      "'mapreduce.admin.user.home.dir' property"
      )
    self.parser.add_argument(
      '-D', metavar="PROP=VALUE", action=type(self).SetProperty,
      help='Set a property value, such as -D mapred.compress.map.output=true'
      )
    # set default properties
    self.properties = {
      'hadoop.pipes.java.recordreader': 'true',
      'hadoop.pipes.java.recordwriter': 'true',
      'mapred.create.symlink': 'yes',
      'mapred.compress.map.output': 'true',
      'bl.libhdfs.opts': '-Xmx48m'
      }
    self.hdfs = None
    self.options = None
    # whether to use our custom Java NoSeparatorTextOutputFormat.
    self.use_no_sep_writer = False

  def parse_cmd_line(self, args=None):
    self.options, self.left_over_args = self.parser.parse_known_args(
      args=args, namespace=type(self).Args()
      )
    # set the job name.  Do it here so the user can override it
    self.properties['mapred.job.name'] = os.path.basename(self.options.module)
    for k, v in self.options.properties.iteritems():
      self.properties[k] = v
    if self.options.num_reducers is None:
      n_red_tasks = type(self).DefaultReduceTasksPerNode * hadut.get_num_nodes()
    else:
      n_red_tasks = self.options.num_reducers
    self.properties['mapred.reduce.tasks'] = n_red_tasks
    self.properties[
      'mapred.textoutputformat.separator'
      ] = self.options.kv_separator
    if self.properties['mapred.textoutputformat.separator'] == '':
      self.use_no_sep_writer = True

  def __write_pipes_script(self, fd):
    ld_path = os.environ.get('LD_LIBRARY_PATH', None)
    pypath = os.environ.get('PYTHONPATH', '')
    fd.write("#!/bin/bash\n")
    fd.write('""":"\n')
    if ld_path:
      fd.write('export LD_LIBRARY_PATH="%s"\n' % ld_path)
    if pypath:
      fd.write('export PYTHONPATH="%s"\n' % pypath)
    # override the script's home directory.
    if (not self.properties.has_key("mapreduce.admin.user.home.dir") and
        os.environ.has_key('HOME') and
        not self.options.no_override_home):
      fd.write('export HOME="%s"\n' % os.environ['HOME'])
    fd.write('exec "%s" -u "$0" "$@"\n' % sys.executable)
    fd.write('":"""\n')
    template_args = {
      'module': os.path.splitext(os.path.basename(self.options.module))[0],
      'map_fn': self.options.map_fn,
      'reduce_fn': self.options.reduce_fn,
      }
    fd.write(PIPES_TEMPLATE % template_args)

  def __validate(self):
    if not os.access(self.options.module, os.R_OK):
      raise RuntimeError("Can't read module file %s" % self.options.module)
    if not self.hdfs.exists(self.options.input):
      raise RuntimeError(
        "Input directory %s doesn't exist." % self.options.input
        )
    if self.hdfs.exists(self.options.output):
      raise RuntimeError(
        "Output directory %s already exists" % self.options.output
        )

  def __find_pydoop_jar(self):
    pydoop_jar_path = os.path.join(
      os.path.dirname(pydoop.__file__), pydoop.__jar_name__
      )
    if os.path.exists(pydoop_jar_path):
      return pydoop_jar_path
    else:
      return None

  def run(self):
    if self.options is None:
      raise RuntimeError("You must call parse_cmd_line before run")
    remote_bin_dir = tempfile.mktemp(
      prefix='pydoop_script_run_dir.', suffix=str(random.random()), dir=''
      )
    remote_pipes_bin = os.path.join(remote_bin_dir, 'pipes_script')
    remote_module = os.path.join(
      remote_bin_dir, os.path.basename(self.options.module)
      )
    try:
      self.hdfs = pydoop.hdfs.hdfs('default', 0)
      self.__validate()
      dist_cache_parameter = "%s#%s" % (
        remote_module, os.path.basename(remote_module)
        )
      if self.properties.get('mapred.cache.files', ''):
        self.properties['mapred.cache.files'] += ',' + dist_cache_parameter
      else:
        self.properties['mapred.cache.files'] = dist_cache_parameter
      pipes_args = self.left_over_args
      if self.use_no_sep_writer:
        pydoop_jar = self.__find_pydoop_jar()
        if pydoop_jar is not None:
          self.properties[
            'mapred.output.format.class'
            ] = 'it.crs4.pydoop.NoSeparatorTextOutputFormat'
          pipes_args.append('-libjars')
          pipes_args.append(pydoop_jar)
        else:
          warnings.warn(
            "Can't find pydoop.jar, output will probably be tab-separated\n"
            )
      try:
        with self.hdfs.open_file(remote_pipes_bin, 'w') as script:
          self.__write_pipes_script(script)
        with self.hdfs.open_file(remote_module, 'w') as module:
          with open(self.options.module) as local_module:
            module.write(local_module.read())
        return hadut.run_pipes(
          remote_pipes_bin, self.options.input, self.options.output,
          more_args=pipes_args, properties=self.properties
          )
      finally:
        try:
          self.hdfs.delete(remote_bin_dir)
        except IOError:
          warnings.warn("Could not delete '%s' from HDFS" % remote_bin_dir)
    finally:
      if self.hdfs:
        tmp = self.hdfs
        self.hdfs = None
        tmp.close()


def main(args):
  script = PydoopScript()
  script.parse_cmd_line(args)
  try:
    print script.run()
  except RuntimeError as e:
    sys.stderr.write("Error running Pydoop script\n%s" % e)
    return 1
  else:
    return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))