import re
import exceptions
from twisted.python import log
from twisted.internet import defer
from buildbot.process.buildstep import LogLineObserver
from buildbot.steps.shell import Test

class MtrTestFailData:
    def __init__(self, testname, variant, result, info, text, callback):
        self.testname = testname
        self.variant = variant
        self.result = result
        self.info = info
        self.text = text
        self.callback = callback

    def add(self, line):
        self.text+= line

    def fireCallback(self):
        return self.callback(self.testname, self.variant, self.result, self.info, self.text)


class MtrLogObserver(LogLineObserver):
    _line_re = re.compile(r"^([-._0-9a-zA-z]+)( '[a-z]+')?\s+\[ (fail|pass) \]\s*(.*)$")
    _line_re2 = re.compile(r"^[-._0-9a-zA-z]+( '[a-z]+')?\s+\[ [-a-z]+ \]")
    _line_re3 = re.compile(r"^\*\*\*Warnings generated in error logs during shutdown after running tests: (.*)")
    _line_re4 = re.compile(r"^The servers were restarted [0-9]+ times$")
    _line_re5 = re.compile(r"^Only\s+[0-9]+\s+of\s+[0-9]+\s+completed.$")
    
    def __init__(self, textLimit=5, testNameLimit=16):
        self.textLimit = textLimit
        self.testNameLimit = testNameLimit
        self.numTests = 0
        self.testFail = None
        self.failList = []
        self.warnList = []
        LogLineObserver.__init__(self)

    def setLog(self, loog):
        LogLineObserver.setLog(self, loog)
        d= loog.waitUntilFinished()
        d.addCallback(lambda l: self.closeTestFail())

    def outLineReceived(self, line):
        stripLine = line.strip("\r\n")
        m = self._line_re.search(stripLine)
        if m:
            testname, variant, result, info = m.groups()
            self.closeTestFail()
            self.numTests += 1
            self.step.setProgress('tests', self.numTests)

            if result == "fail":
                if variant == None:
                    variant = ""
                else:
                    variant = variant[2:-1]
                self.openTestFail(testname, variant, result, info, stripLine + "\n")

        else:
            m = self._line_re3.search(stripLine)
            if m:
                stuff = m.group(1)
                self.closeTestFail()
                testList = stuff.split(" ")
                self.doCollectWarningTests(testList)

            elif (self._line_re2.search(stripLine) or
                  self._line_re4.search(stripLine) or
                  self._line_re5.search(stripLine) or
                  stripLine == "Test suite timeout! Terminating..." or
                  stripLine.startswith("mysql-test-run: *** ERROR: Not all tests completed") or
                  (stripLine.startswith("------------------------------------------------------------")
                   and self.testFail != None)):
                self.closeTestFail()

            else:
                self.addTestFailOutput(stripLine + "\n")

    def openTestFail(self, testname, variant, result, info, line):
        self.testFail = MtrTestFailData(testname, variant, result, info, line, self.doCollectTestFail)

    def addTestFailOutput(self, line):
        if self.testFail != None:
            self.testFail.add(line)

    def closeTestFail(self):
        if self.testFail != None:
            self.testFail.fireCallback()
            self.testFail = None

    def addToText(self, src, dst):
        lastOne = None
        count = 0
        for t in src:
            if t != lastOne:
                dst.append(t)
                count += 1
                if count >= self.textLimit:
                    break

    def makeText(self, done):
        if done:
            text = ["test"]
        else:
            text = ["testing"]
        fails = self.failList[:]
        fails.sort()
        self.addToText(fails, text)
        warns = self.warnList[:]
        warns.sort()
        self.addToText(warns, text)
        return text

    # Update waterfall status.
    def updateText(self):
        self.step.step_status.setText(self.makeText(False))

    strip_re = re.compile(r"^[a-z]+\.")

    def displayTestName(self, testname):

        displayTestName = self.strip_re.sub("", testname)
        
        if len(displayTestName) > self.testNameLimit:
            displayTestName = displayTestName[:(self.testNameLimit-2)] + "..."
        return displayTestName

    def doCollectTestFail(self, testname, variant, result, info, text):
        self.failList.append("F:" + self.displayTestName(testname))
        self.updateText()
        self.collectTestFail(testname, variant, result, info, text)

    def doCollectWarningTests(self, testList):
        for t in testList:
            self.warnList.append("W:" + self.displayTestName(t))
        self.updateText()
        self.collectWarningTests(testList)

    # These two methods are overridden to actually do something with the data.
    def collectTestFail(self, testname, variant, result, info, text):
        pass
    def collectWarningTests(self, testList):
        pass

class MTR(Test):
    def __init__(self, dbpool=None, test_type="mysql-test-run", test_info="",
                 autoCreateTables=False, textLimit=5, testNameLimit=16, **kwargs):
        Test.__init__(self, **kwargs)
        self.dbpool = dbpool
        self.test_type = test_type
        self.test_info = test_info
        self.autoCreateTables = autoCreateTables
        self.textLimit = textLimit
        self.testNameLimit = testNameLimit
        self.progressMetrics += ('tests',)
        self.addFactoryArguments(dbpool=self.dbpool,
                                 test_type=self.test_type,
                                 test_info=self.test_info,
                                 autoCreateTables=self.autoCreateTables,
                                 textLimit=self.textLimit,
                                 testNameLimit=self.testNameLimit)

    def start(self):
        self.myMtr = self.MyMtrLogObserver(textLimit=self.textLimit,
                                           testNameLimit=self.testNameLimit)
        self.addLogObserver("stdio", self.myMtr)
        # Insert a row for this test run into the database and set up
        # build ties, then start the command proper.
        d = self.registerInDB()
        d.addCallback(self.afterRegisterInDB)
        d.addErrback(self.failed)

    def getText(self, command, results):
        return self.myMtr.makeText(True)

    def registerInDB(self):
        insert_id = 0
        if self.dbpool:
            return self.dbpool.runInteraction(self.doRegisterInDB)
        else:
            return defer.succeed(0)

    # The real database work is done in a thread in a synchronous way.
    def doRegisterInDB(self, txn):
        # Auto create tables.
        # This is off by default, as it gives warnings in log file
        # about tables already existing (and I did not find the issue
        # important enough to find a better fix).
        if self.autoCreateTables:
            txn.execute("""
CREATE TABLE IF NOT EXISTS test_run(
    id INT PRIMARY KEY AUTO_INCREMENT,
    branch VARCHAR(100),
    revision VARCHAR(32) NOT NULL,
    platform VARCHAR(100) NOT NULL,
    dt TIMESTAMP NOT NULL,
    bbnum INT NOT NULL,
    typ VARCHAR(32) NOT NULL,
    INFO VARCHAR(255),
    KEY (branch, revision),
    KEY (dt),
    KEY (platform, bbnum)
) ENGINE=innodb
""")
            txn.execute("""
CREATE TABLE IF NOT EXISTS test_failure(
    test_run_id INT NOT NULL,
    test_name VARCHAR(100) NOT NULL,
    test_variant VARCHAR(16) NOT NULL,
    info_text VARCHAR(255),
    failure_text TEXT,
    PRIMARY KEY (test_run_id, test_name, test_variant)
) ENGINE=innodb
""")
            txn.execute("""
CREATE TABLE IF NOT EXISTS test_warnings(
    test_run_id INT NOT NULL,
    list_id INT NOT NULL,
    list_idx INT NOT NULL,
    test_name VARCHAR(100) NOT NULL,
    PRIMARY KEY (test_run_id, list_id, list_idx)
) ENGINE=innodb
""")

        revision = None
        try:
            revision = self.getProperty("got_revision")
        except exceptions.KeyError:
            revision = self.getProperty("revision")
        txn.execute("""
INSERT INTO test_run(branch, revision, platform, dt, bbnum, typ, info)
VALUES (%s, %s, %s, CURRENT_TIMESTAMP(), %s, %s, %s)
""", (self.getProperty("branch"), revision,
      self.getProperty("buildername"), self.getProperty("buildnumber"),
      self.test_type, self.test_info))

        return txn.lastrowid

    def afterRegisterInDB(self, insert_id):
        self.setProperty("mtr_id", insert_id)
        self.setProperty("mtr_warn_id", 0)

        Test.start(self)

    class MyMtrLogObserver(MtrLogObserver):
        def collectTestFail(self, testname, variant, result, info, text):
            # Insert asynchronously into database.
            dbpool = self.step.dbpool
            run_id = self.step.getProperty("mtr_id")
            if dbpool == None:
                return defer.succeed(None)
            if variant == None:
                variant = ""
            d = dbpool.runQuery("""
INSERT INTO test_failure(test_run_id, test_name, test_variant, info_text, failure_text)
VALUES (%s, %s, %s, %s, %s)
""", (run_id, testname, variant, info, text))

            d.addErrback(self.reportError)
            return d

        def collectWarningTests(self, testList):
            # Insert asynchronously into database.
            dbpool = self.step.dbpool
            if dbpool == None:
                return defer.succeed(None)
            run_id = self.step.getProperty("mtr_id")
            warn_id = self.step.getProperty("mtr_warn_id")
            self.step.setProperty("mtr_warn_id", warn_id + 1)
            q = ("INSERT INTO test_warnings(test_run_id, list_id, list_idx, test_name) " +
                 "VALUES " + ", ".join(map(lambda x: "(%s, %s, %s, %s)", testList)))
            v = []
            idx = 0
            for t in testList:
                v.extend([run_id, warn_id, idx, t])
                idx = idx + 1
            d = dbpool.runQuery(q, tuple(v))
            d.addErrback(self.reportError)
            return d

        def reportError(self, err):
            log.msg("Error in async insert into database: %s" % err)
