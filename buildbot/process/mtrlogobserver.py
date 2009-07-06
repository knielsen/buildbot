import re
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
    
    def __init__(self):
        self.numTests = 0
        self.testFail = None
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
                self.collectWarningTests(testList)

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
        self.testFail = MtrTestFailData(testname, variant, result, info, line, self.collectTestFail)

    def addTestFailOutput(self, line):
        if self.testFail != None:
            self.testFail.add(line)

    def closeTestFail(self):
        if self.testFail != None:
            self.testFail.fireCallback()
            self.testFail = None

    # These two methods are overridden to actually do something with the data.
    def collectTestFail(self, testname, variant, result, info, text):
        pass
    def collectWarningTests(self, testList):
        pass

class MTR(Test):
    def __init__(self, **kwargs):
        Test.__init__(self, **kwargs)
        self.addLogObserver("stdio", self.MyMtrLogObserver())
        self.progressMetrics += ('tests',)

    def start(self):
        # Insert a row for this test run into the database and set up
        # build ties, then start the command proper.
        d = self.registerInDB()
        d.addCallback(self.afterRegisterInDB)
        d.addErrback(self.failed)

    def registerInDB(self):
        # ToDo
        log.msg("registerInDB()")
        return defer.succeed(0)

    def afterRegisterInDB(self, insert_id):
        self.setProperty("mtr_id", insert_id)
        # ToDo: maybe more properties, like test type?

        Test.start(self)

    class MyMtrLogObserver(MtrLogObserver):
        def collectTestFail(self, testname, variant, result, info, text):
            # ToDo
            log.msg("FAIL: %s '%s' %s" % (testname, variant, info))
        def collectWarningTests(self, testList):
            # ToDo
            log.msg("FAILLIST: %s" % (" ".join(testList)))
