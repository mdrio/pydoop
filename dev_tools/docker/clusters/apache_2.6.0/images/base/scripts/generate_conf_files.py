import sys
import os
import xml.etree.cElementTree as ET


def add_property(conf, name, value):
    prop = ET.SubElement(conf, 'property')
    ET.SubElement(prop, 'name').text = name
    ET.SubElement(prop, 'value').text = value


def write_xml(root, fname):
    tree = ET.ElementTree(root)
    with open(fname, 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>')
        tree.write(f)


def generate_xml_conf_file(fname, props):
    root = ET.Element("configuration")
    for name, value in props:
        add_property(root, name, value)
    write_xml(root, fname)


def generate_core_site(fname):
    hostname = 'namenode'
    generate_xml_conf_file(fname, (
        ('fs.defaultFS', 'hdfs://%s:8020' % hostname),
        ('hadoop.tmp.dir', 'file://' + os.environ['HADOOP_TMP_DIR'])
        ))


def generate_hdfs_site(fname):
    generate_xml_conf_file(fname, (
        ('dfs.replication', '1'),
        ('dfs.namenode.name.dir', 'file://' + os.environ['DFS_NAME_DIR']),
        ('dfs.datanode.data.dir', 'file://' + os.environ['DFS_DATA_DIR']),
        ('dfs.namenode.checkpoint.dir', os.environ['DFS_CHECKPOINT_DIR']),
        ('dfs.namenode.checkpoint.edits.dir',
            os.environ['DFS_CHECKPOINT_DIR']),
        ))


def generate_yarn_site(fname):
    generate_xml_conf_file(fname, (
        ('yarn.resourcemanager.hostname', 'resourcemanager'),
        ('yarn.nodemanager.hostname', 'nodemanager'),
        ('yarn.nodemanager.aux-services', 'mapreduce_shuffle'),
        ('yarn.nodemanager.aux-services.mapreduce.shuffle.class',
            'org.apache.hadoop.mapred.ShuffleHandler'),
        # seconds to delay before deleting application
        # localized logs and files. > 0 if debugging.
        ('yarn.nodemanager.delete.debug-delay-sec', '600'),
        ('yarn.nodemanager.log-dirs',
            'file://' + os.environ['YARN_LOCAL_LOG_DIR']),
        ('yarn.log.dir', os.environ['YARN_LOG_DIR']),
        ('yarn.nodemanager.remote-app-log-dir',
            os.environ['YARN_REMOTE_APP_LOG_DIR']),
        ('yarn.log-aggregation-enable', 'true'),
        # ('yarn.log-aggregation.retain-seconds', '360000'),
        # ('yarn.log-aggregation.retain-check-interval-seconds', '360'),
        # ('yarn.log.server.url', 'http://historyserver:19888'),
        ))


def generate_mapred_site(fname):
    generate_xml_conf_file(fname, (
        ('mapreduce.framework.name', 'yarn'),

        # MRv1
        ('mapreduce.jobtracker.address', 'resourcemanager:8021'),
        ('mapreduce.jobtracker.http.address', 'resourcemanager:50030'),
        ('mapreduce.tasktracker.http.address', 'nodemanager:50060'),

        # History Server
        ('mapreduce.jobhistory.address', 'historyserver:10020'),
        ('mapreduce.jobhistory.webapp.address', 'historyserver:19888'),
        ('mapreduce.jobhistory.intermediate-done-dir',
            os.environ['MAPRED_JH_INTERMEDIATE_DONE_DIR']),
        ('mapreduce.jobhistory.done-dir', os.environ['MAPRED_JH_DONE_DIR']),
        ))


def generate_capacity_scheduler(fname):
    generate_xml_conf_file(fname, (
        ('yarn.scheduler.capacity.resource-calculator',
         'org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator'),
        ('yarn.scheduler.capacity.root.queues', 'default'),
        ('yarn.scheduler.capacity.root.default.capacity', '100'),
        ('yarn.scheduler.capacity.root.default.user-limit-factor', '1'),
        ('yarn.scheduler.capacity.root.default.maximum-capacity', '100'),
        ('yarn.scheduler.capacity.root.default.state', 'RUNNING'),
        ('yarn.scheduler.capacity.root.default.acl_submit_applications', '*'),
        ('yarn.scheduler.capacity.root.default.acl_administer_queue', '*'),
        ('yarn.scheduler.capacity.node-locality-delay', '40')))


def main(argv):
    target_dir = argv[1]
    generate_core_site(os.path.join(target_dir, 'core-site.xml'))
    generate_hdfs_site(os.path.join(target_dir, 'hdfs-site.xml'))
    generate_yarn_site(os.path.join(target_dir, 'yarn-site.xml'))
    generate_mapred_site(os.path.join(target_dir, 'mapred-site.xml'))
    generate_capacity_scheduler(os.path.join(target_dir,
                                             'capacity-scheduler.xml'))


main(sys.argv)
