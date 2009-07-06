# -*- test-case-name: buildbot.test.test_buildstep -*-

# test cases for buildbot.process.buildstep

from twisted.trial import unittest

from buildbot import interfaces
from buildbot.process import buildstep
from buildbot.process import mtrlogobserver

# have to subclass LogObserver in order to test it, since the default
# implementations of outReceived() and errReceived() do nothing
class MyLogObserver(buildstep.LogObserver):
    def __init__(self):
        self._out = []                  # list of chunks
        self._err = []

    def outReceived(self, data):
        self._out.append(data)

    def errReceived(self, data):
        self._err.append(data)

class ObserverTestCase(unittest.TestCase):
    observer_cls = None                 # must be set by subclass

    def setUp(self):
        self.observer = self.observer_cls()

    def _logStdout(self, chunk):
        # why does LogObserver.logChunk() take 'build', 'step', and
        # 'log' arguments when it clearly doesn't use them for anything?
        self.observer.logChunk(None, None, None, interfaces.LOG_CHANNEL_STDOUT, chunk)

    def _logStderr(self, chunk):
        self.observer.logChunk(None, None, None, interfaces.LOG_CHANNEL_STDERR, chunk)

    def _assertStdout(self, expect_lines):
        self.assertEqual(self.observer._out, expect_lines)

    def _assertStderr(self, expect_lines):
        self.assertEqual(self.observer._err, expect_lines)

class LogObserver(ObserverTestCase):

    observer_cls = MyLogObserver

    def testLogChunk(self):
        self._logStdout("foo")
        self._logStderr("argh")
        self._logStdout(" wubba\n")
        self._logStderr("!!!\n")

        self._assertStdout(["foo", " wubba\n"])
        self._assertStderr(["argh", "!!!\n"])

# again, have to subclass LogLineObserver in order to test it, because the
# default implementations of data-receiving methods are empty
class MyLogLineObserver(buildstep.LogLineObserver):
    def __init__(self):
        #super(MyLogLineObserver, self).__init__()
        buildstep.LogLineObserver.__init__(self)

        self._out = []                  # list of lines
        self._err = []

    def outLineReceived(self, line):
        self._out.append(line)

    def errLineReceived(self, line):
        self._err.append(line)

class LogLineObserver(ObserverTestCase):
    observer_cls = MyLogLineObserver

    def testLineBuffered(self):
        # no challenge here: we feed it chunks that are already lines
        # (like a program writing to stdout in line-buffered mode)
        self._logStdout("stdout line 1\n")
        self._logStdout("stdout line 2\n")
        self._logStderr("stderr line 1\n")
        self._logStdout("stdout line 3\n")

        self._assertStdout(["stdout line 1",
                            "stdout line 2",
                            "stdout line 3"])
        self._assertStderr(["stderr line 1"])
        
    def testShortBrokenLines(self):
        self._logStdout("stdout line 1 starts ")
        self._logStderr("an intervening line of error\n")
        self._logStdout("and continues ")
        self._logStdout("but finishes here\n")
        self._logStderr("more error\n")
        self._logStdout("and another line of stdout\n")

        self._assertStdout(["stdout line 1 starts and continues but finishes here",
                            "and another line of stdout"])
        self._assertStderr(["an intervening line of error",
                            "more error"])

    def testLongLine(self):
        chunk = "." * 1024
        self._logStdout(chunk)
        self._logStdout(chunk)
        self._logStdout(chunk)
        self._logStdout(chunk)
        self._logStdout(chunk)
        self._logStdout("\n")

        self._assertStdout([chunk * 5])
        self._assertStderr([])

    def testBigChunk(self):
        chunk = "." * 5000
        self._logStdout(chunk)
        self._logStdout("\n")

        self._assertStdout([chunk])
        self._assertStderr([])

    def testReallyLongLine(self):
        # A single line of > 16384 bytes is dropped on the floor (bug #201).
        # In real life, I observed such a line being broken into chunks of
        # 4095 bytes, so that's how I'm breaking it here.
        self.observer.setMaxLineLength(65536)
        chunk = "." * 4095
        self._logStdout(chunk)
        self._logStdout(chunk)
        self._logStdout(chunk)
        self._logStdout(chunk)          # now we're up to 16380 bytes
        self._logStdout("12345\n")

        self._assertStdout([chunk*4 + "12345"])
        self._assertStderr([])

class MyMtrLogObserver(mtrlogobserver.MtrLogObserver):
    def __init__(self):
        mtrlogobserver.MtrLogObserver.__init__(self)
        self.testFails = []
        self.testWarnLists = []
        # We don't have a buildstep in self.step.
        # So we'll just install ourself there, so we can check the call of
        # setProgress().
        self.step = self
        self.progresses = []

    def setProgress(self, type, value):
        self.progresses.append((type, value))

    def collectTestFail(self, testname, variant, result, info, text):
        self.testFails.append((testname, variant, result, info, text))

    def collectWarningTests(self, testList):
        self.testWarnLists.append(testList)

class MtrLogObserver(ObserverTestCase):
    observer_cls = MyMtrLogObserver

    def test1(self):
        self._logStdout("""
Logging: mysql-test-run.pl  --mem --parallel=3 --valgrind --force --skip-ndb
MySQL Version 5.1.35
==============================================================================

TEST                                      RESULT   TIME (ms)
------------------------------------------------------------

worker[2] Using MTR_BUILD_THREAD 251, with reserved ports 12510..12519
worker[3] Using MTR_BUILD_THREAD 252, with reserved ports 12520..12529
binlog.binlog_multi_engine               [ skipped ]  No ndbcluster tests(--skip-ndbcluster)
binlog.binlog_row_innodb_stat 'stmt'     [ skipped ]  Doesn't support --binlog-format='statement'
rpl.rpl_ssl 'stmt'                       [ pass ]  13697
rpl.rpl_ssl 'row'                        [ pass ]  13976
***Warnings generated in error logs during shutdown after running tests: rpl.rpl_ssl
rpl.rpl_ssl 'mix'                        [ pass ]  13308
main.pool_of_threads                     [ pass ]  575885
------------------------------------------------------------
The servers were restarted 613 times
Spent 28765.083 of 15139 seconds executing testcases

mysql-test-run: *** ERROR: There were errors/warnings in server logs after running test cases.
All 1002 tests were successful.

Errors/warnings were found in logfiles during server shutdown after running the
following sequence(s) of tests:
    rpl.rpl_ssl
""")
        self.assertEqual(self.observer.progresses,
                         map((lambda (x): ('tests', x)), [1,2,3,4]))
        self.assertEqual(self.observer.testWarnLists, [["rpl.rpl_ssl"]])
        self.assertEqual(self.observer.testFails, [])

    def test2(self):
        self._logStdout("""
Logging: mysql-test-run.pl  --force --skip-ndb
==============================================================================

TEST                                      RESULT   TIME (ms)
------------------------------------------------------------

worker[1] Using MTR_BUILD_THREAD 250, with reserved ports 12500..12509
binlog.binlog_multi_engine               [ skipped ]  No ndbcluster tests(--skip-ndbcluster)
rpl.rpl_sp 'mix'                         [ pass ]   8117

MTR's internal check of the test case 'rpl.rpl_sp' failed.
This means that the test case does not preserve the state that existed
before the test case was executed.  Most likely the test case did not
do a proper clean-up.
This is the diff of the states of the servers before and after the
test case was executed:
mysqltest: Logging to '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/check-mysqld_2.log'.
mysqltest: Results saved in '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/check-mysqld_2.result'.
mysqltest: Connecting to server localhost:12502 (socket /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/mysqld.2.sock) as 'root', connection 'default', attempt 0 ...
mysqltest: ... Connected.
mysqltest: Start processing test commands from './include/check-testcase.test' ...
mysqltest: ... Done processing test commands.
--- /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/check-mysqld_2.result	2009-06-18 16:49:19.000000000 +0300
+++ /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/check-mysqld_2.reject	2009-06-18 16:49:29.000000000 +0300
@@ -523,7 +523,7 @@
 mysql.help_keyword	864336512
 mysql.help_relation	2554468794
 mysql.host	0
-mysql.proc	3342691386
+mysql.proc	3520745907
 mysql.procs_priv	0
 mysql.tables_priv	0
 mysql.time_zone	2420313365

mysqltest: Result content mismatch

not ok

rpl.rpl_sp_effects 'row'                 [ pass ]   3789
rpl.rpl_temporary_errors 'mix'           [ fail ]
        Test ended at 2009-06-18 16:21:28

CURRENT_TEST: rpl.rpl_temporary_errors


Could not execute 'check-warnings' for testcase 'rpl.rpl_temporary_errors' (res: 1):
mysqltest: Logging to ''.
mysqltest: Results saved in ''.
mysqltest: Connecting to server localhost:12502 (socket /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/mysqld.2.sock) as 'root', connection 'default', attempt 0 ...
mysqltest: ... Connected.
mysqltest: Start processing test commands from './include/check-warnings.test' ...
mysqltest: At line 56: query 'call mtr.check_warnings(@result)' failed: 2013: Lost connection to MySQL server during query
not ok


 - saving '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/rpl.rpl_temporary_errors-mix/' to '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/rpl.rpl_temporary_errors-mix/'
 - found 'core.17307' (0/5)

Trying 'dbx' to get a backtrace

Trying 'gdb' to get a backtrace
Core generated by '/home/archivist/archivist-cnc/archivist-cnc/build/sql/mysqld'
Output from gdb follows. The first stack trace is from the failing thread.
The following stack traces are from all threads (so the failing one is
duplicated).
--------------------------

warning: Can't read pathname for load map: Input/output error.
Core was generated by `/home/archivist/archivist-cnc/archivist-cnc/build/sql/mysqld --defaults-group-s'.
Program terminated with signal 11, Segmentation fault.
[New process 17388]
[New process 17428]
[New process 17387]
[New process 17322]
[New process 17319]
[New process 17317]
[New process 17316]
[New process 17314]
[New process 17312]
[New process 17310]
[New process 17307]
#0  0xb7fa3410 in __kernel_vsyscall ()


Retrying test, attempt(2/3)...

***Warnings generated in error logs during shutdown after running tests: rpl.rpl_temporary_errors
rpl.rpl_temporary_errors 'mix'           [ retry-pass ]   2108

Retrying test, attempt(3/3)...

rpl.rpl_trunc_temp 'stmt'                [ pass ]   2576
rpl.rpl_temporary_errors 'mix'           [ retry-pass ]   2317
rpl.rpl_trunc_temp 'mix'                 [ pass ]   3933
main.information_schema                  [ pass ]  106092
timer 5953: expired after 900 seconds
worker[1] Trying to dump core for [mysqltest - pid: 5975, winpid: 5975]
worker[1] Trying to dump core for [mysqld.1 - pid: 27656, winpid: 27656]
main.information_schema_all_engines      [ fail ]  timeout after 900 seconds
        Test ended at 2009-06-18 18:37:25

Test case timeout after 900 seconds

== /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/information_schema_all_engines.log == 
STATISTICS	TABLE_SCHEMA
TABLES	TABLE_SCHEMA
TABLE_CONSTRAINTS	CONSTRAINT_SCHEMA
TABLE_PRIVILEGES	TABLE_SCHEMA
TRIGGERS	TRIGGER_SCHEMA
USER_PRIVILEGES	GRANTEE
VIEWS	TABLE_SCHEMA
INNODB_BUFFER_POOL_PAGES	page_type
PBXT_STATISTICS	ID
INNODB_CMP	page_size
INNODB_RSEG	rseg_id
XTRADB_ENHANCEMENTS	name
INNODB_BUFFER_POOL_PAGES_INDEX	schema_name
INNODB_BUFFER_POOL_PAGES_BLOB	space_id
INNODB_TRX	trx_id
INNODB_CMP_RESET	page_size
INNODB_LOCK_WAITS	requesting_trx_id
INNODB_CMPMEM_RESET	page_size
INNODB_LOCKS	lock_id
INNODB_CMPMEM	page_size

 == /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/analyze-timeout-mysqld.1.err ==
mysqltest: Could not open connection 'default' after 500 attempts: 2002 Can't connect to local MySQL server through socket '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/mysqld.1.sock' (111)


 - saving '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/main.information_schema_all_engines/' to '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/main.information_schema_all_engines/'
 - found 'core.27657' (1/5)

Trying 'dbx' to get a backtrace

Trying 'gdb' to get a backtrace


Retrying test, attempt(2/3)...

***Warnings generated in error logs during shutdown after running tests: main.handler_myisam main.ctype_ujis_ucs2 main.ctype_recoding
main.information_schema_chmod            [ pass ]     84
main.information_schema_all_engines      [ retry-pass ]  737620

Retrying test, attempt(3/3)...

main.information_schema_db               [ pass ]   3288
main.information_schema_part             [ pass ]   1520
main.information_schema_all_engines      [ retry-pass ]  817929
rpl.rpl_circular_for_4_hosts 'stmt'      [ pass ]  344547
timer 21612: expired after 21600 seconds
Test suite timeout! Terminating...

Only  1370  of 1379 completed.
mysql-test-run: *** ERROR: Not all tests completed
""")
        self.assertEqual(self.observer.progresses,
                         map((lambda (x): ('tests', x)), [1,2,3,4,5,6,7,8,9,10,11]))
        self.assertEqual(self.observer.testWarnLists,
                         [["rpl.rpl_temporary_errors"],
                          ["main.handler_myisam", "main.ctype_ujis_ucs2", "main.ctype_recoding"]])
        failtext1 = """rpl.rpl_temporary_errors 'mix'           [ fail ]
        Test ended at 2009-06-18 16:21:28

CURRENT_TEST: rpl.rpl_temporary_errors


Could not execute 'check-warnings' for testcase 'rpl.rpl_temporary_errors' (res: 1):
mysqltest: Logging to ''.
mysqltest: Results saved in ''.
mysqltest: Connecting to server localhost:12502 (socket /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/mysqld.2.sock) as 'root', connection 'default', attempt 0 ...
mysqltest: ... Connected.
mysqltest: Start processing test commands from './include/check-warnings.test' ...
mysqltest: At line 56: query 'call mtr.check_warnings(@result)' failed: 2013: Lost connection to MySQL server during query
not ok


 - saving '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/rpl.rpl_temporary_errors-mix/' to '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/rpl.rpl_temporary_errors-mix/'
 - found 'core.17307' (0/5)

Trying 'dbx' to get a backtrace

Trying 'gdb' to get a backtrace
Core generated by '/home/archivist/archivist-cnc/archivist-cnc/build/sql/mysqld'
Output from gdb follows. The first stack trace is from the failing thread.
The following stack traces are from all threads (so the failing one is
duplicated).
--------------------------

warning: Can't read pathname for load map: Input/output error.
Core was generated by `/home/archivist/archivist-cnc/archivist-cnc/build/sql/mysqld --defaults-group-s'.
Program terminated with signal 11, Segmentation fault.
[New process 17388]
[New process 17428]
[New process 17387]
[New process 17322]
[New process 17319]
[New process 17317]
[New process 17316]
[New process 17314]
[New process 17312]
[New process 17310]
[New process 17307]
#0  0xb7fa3410 in __kernel_vsyscall ()


Retrying test, attempt(2/3)...

"""
        failtext2 = """main.information_schema_all_engines      [ fail ]  timeout after 900 seconds
        Test ended at 2009-06-18 18:37:25

Test case timeout after 900 seconds

== /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/information_schema_all_engines.log == 
STATISTICS	TABLE_SCHEMA
TABLES	TABLE_SCHEMA
TABLE_CONSTRAINTS	CONSTRAINT_SCHEMA
TABLE_PRIVILEGES	TABLE_SCHEMA
TRIGGERS	TRIGGER_SCHEMA
USER_PRIVILEGES	GRANTEE
VIEWS	TABLE_SCHEMA
INNODB_BUFFER_POOL_PAGES	page_type
PBXT_STATISTICS	ID
INNODB_CMP	page_size
INNODB_RSEG	rseg_id
XTRADB_ENHANCEMENTS	name
INNODB_BUFFER_POOL_PAGES_INDEX	schema_name
INNODB_BUFFER_POOL_PAGES_BLOB	space_id
INNODB_TRX	trx_id
INNODB_CMP_RESET	page_size
INNODB_LOCK_WAITS	requesting_trx_id
INNODB_CMPMEM_RESET	page_size
INNODB_LOCKS	lock_id
INNODB_CMPMEM	page_size

 == /home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/analyze-timeout-mysqld.1.err ==
mysqltest: Could not open connection 'default' after 500 attempts: 2002 Can't connect to local MySQL server through socket '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/tmp/mysqld.1.sock' (111)


 - saving '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/main.information_schema_all_engines/' to '/home/archivist/archivist-cnc/archivist-cnc/build/mysql-test/var/log/main.information_schema_all_engines/'
 - found 'core.27657' (1/5)

Trying 'dbx' to get a backtrace

Trying 'gdb' to get a backtrace


Retrying test, attempt(2/3)...

"""
        self.assertEqual(self.observer.testFails,
                         [ ("rpl.rpl_temporary_errors", "mix", "fail", "", failtext1),
                           ("main.information_schema_all_engines", "", "fail", "timeout after 900 seconds", failtext2)
                           ])

class RemoteShellTest(unittest.TestCase):
    def testRepr(self):
        # Test for #352
        rsc = buildstep.RemoteShellCommand('.', ('sh', 'make'))
        testval = repr(rsc)
        rsc = buildstep.RemoteShellCommand('.', ['sh', 'make'])
        testval = repr(rsc)
        rsc = buildstep.RemoteShellCommand('.', 'make')
        testval = repr(rsc)
