import os
import sys
import numpy as np
import pandas as pd
import logging as log
import datetime as dt
import matplotlib.pyplot as plt
from textwrap import TextWrapper as txwr

import galaxy
import post_process as pp

Galaxy = galaxy.Galaxy

# Create the /Logs folder for the root directory if it doesn't already exist
if not os.path.isdir("Logs") :
    os.mkdir("Logs")

def dateFmt () :
    """Returns the date component of the run log file"""
    dtStr = str(dt.datetime.now())
    dtStr = dtStr[:dtStr.find('.')]
    dtStr = dtStr.replace(' ', '_')
    return dtStr

# Set the logger for this run of classifications
runlog = log.getLogger(__name__)
runlog.setLevel(log.WARNING)
runLogPath = "Logs/run_{}.log".format(dateFmt())
fileHandler = log.FileHandler(runLogPath)
fileHandler.setFormatter(log.Formatter("%(levelname)s : RUN_INIT : %(asctime)s : %(message)s",
                         datefmt='%m/%d/%Y %I:%M:%S %p'))

# Ensures that there is only one fileHandler for the current logger
for h in runlog.handlers :
    runlog.removeHandler(h)

runlog.addHandler(fileHandler)
runlog.info("Batch runner started!")

sys.setrecursionlimit(10**6)

def logFixFmt (fix, k=50) :
    """ Formats error messages for the run logger """
    return  2*(k*"#" + '\n') + txwr(width=k).fill(text=fix) + '\n' + 2*(k*"#" + '\n')

class Batch () :
    """
    Class that loads the FITS data corresponding to a .csv file of SDSS objIDs
    and performs DAGN classification on them
    """

    batchRoot = "Batches"

    def getBatch (batchName, bands=Galaxy.default_bands, rad=40, csv=None) :
        """ Class method to get a batch """

        try :
            batch = Batch(batchName, (batchName + ".csv") if csv is None else csv,
                    bands, rad)
        except (FileNotFoundError, ValueError) as e :
            print("Error initialising batch!")
            print("Kindly check the latest message in the logfile '{}' for a fix.".format(
                os.path.join(os.getcwd(), runLogPath)
            ))
            print("Abort!")
            batch = None
        finally :
            return batch

    #############################################################################################################
    #############################################################################################################

    def __prelude__ (self) :
        """
        Sets up files and folders and checks for existence of
        folder indicated by attribute batchName and the 'csv' filename
        """

        # To access fileHandler of the logger
        global fileHandler
        fileHandler.setFormatter(log.Formatter("%(levelname)s : RUN_INIT : %(asctime)s : %(message)s",
                             datefmt='%m/%d/%Y %I:%M:%S %p'))

        # Checks if the batchRoot directory has been created at the root directory
        runlog.info("Checking environment for the new batch.")
        if not os.path.isdir(Batch.batchRoot) :
            runlog.critical("Data folder not found!\n\n{}".format(logFixFmt(
                "Please create a folder named 'Data' in the notebook directory and rerun!"
            )))
            raise FileNotFoundError

        # Checks if batchName folder exists in batchRoot
        if not os.path.isdir(self.batchFold) :
            runlog.critical("Batch folder not found\n\n{}".format(logFixFmt(
                "Please create a folder for the batch at '{}' and rerun!".format(self.batchFold)
            )))
            raise FileNotFoundError

        ######################################################################
        # Checks if the .csv file exists. If the 'csv' argument is None, the
        # name of the .csv file is taken to be the same name as its containing
        # folder
        ######################################################################
        if not os.path.exists(self.csvPath) :
            runlog.critical("Batch .csv file at path '{}' not found\n\n{}".format(self.batchFold, logFixFmt(
                "Please supply the name of the appropriate .csv file and rerun!"
            )))
            raise FileNotFoundError

        ######################################################################
        # Changing name of the run log fileHandler to reflect the batch it is
        # presently handling
        ######################################################################
        runlog.info("Valid environment! Changing log format to handle batch '{}'".format(self.batchName))
        fileHandler.setFormatter(log.Formatter("%(levelname)s : {} : %(asctime)s : %(message)s".format(self.batchName),
                         datefmt='%m/%d/%Y %I:%M:%S %p'))

        # Ensures only one fileHandler exists
        for h in runlog.handlers :
            runlog.removeHandler(h)
        runlog.addHandler(fileHandler)

        ######################################################################
        # Creates a /FITS folder in the batch folder where all the FITS files will
        # be stored
        ######################################################################
        if not os.path.exists(self.fitsFold) :
            os.mkdir(self.fitsFold)
            runlog.info("Created FITS folder for batch")
        else :
            runlog.info("FITS folder for the batch already exists")

        if not os.path.isdir(self.resFold) :
            os.mkdir(self.resFold)
            runlog.info("Created results folder for batch")
        else :
            runlog.info("Results folder for the batch already exists")

    def __setLoggers__ (self) :
        """
        Sets a logger to record the results as a .csv file
        Can do because logging module of Python is thread-safe
        """

        def setLogger (csvpath, loggername, headerline, runlogtype) :
            """ Internal function to get the logger """

            # Creating the .csv file for results
            writeHeader = False if os.path.exists(csvpath) else True
            logger = log.getLogger(loggername)
            logger.setLevel(log.INFO)
            fh = log.FileHandler(csvpath)
            fh.setFormatter(log.Formatter("%(message)s"))

            # Ensuring only one file handler exists
            for h in logger.handlers :
                logger.removeHandler(h)
            logger.addHandler(fh)

            if writeHeader :
                logger.info(headerline)
                runlog.info(f"Created {runlogtype} csv")
            else :
                runlog.info(f"{runlogtype} csv already exists")

            return logger

        self.reslog = setLogger(self.resCsvPath,
            self.batchName + "_result",
            "objid,u-type,u-peaks,g-type,g-peaks,r-type,r-peaks,i-type,i-peaks",
            "result"
        )
        self.purelog = setLogger(self.pureCsvPath,
            self.batchName + "_pure",
            "objid,bands,pid1,pid2",
            "pure"
        )
        self.impurelog = setLogger(self.impureCsvPath,
            self.batchName + "_impure",
            "objid,u-type,u-peaks,g-type,g-peaks,r-type,r-peaks,i-type,i-peaks",
            "impure"
        )

    def __setBatchList__ (self) :
        """ Sets the list of galaxies to classify -
            1. Reads the main .csv file
            2. Reads the .csv file which contains results of
            already classified galaxies
            3. The set difference of (1) and (2) are the galaxies
            yet to be classified
        """

        # try block to read the master .csv file
        try :
            df = pd.read_csv(self.csvPath, dtype=object, usecols=["objid", "ra", "dec"])
        except ValueError as e :
            runlog.critical("Invalid columns in .csv file\n\n{}".format(logFixFmt(
                "Please ensure columns 'objid', 'ra' and 'dec' are present in the .csv \
                file (in that order) and rerun!"
                )))
            raise e

        # try block to read the result .csv file
        try :
            resIDs = [] if not os.path.exists(self.resCsvPath) else\
                    list(pd.read_csv(self.resCsvPath, dtype=object)['objid'])
        except ValueError as e :
            runlog.critical("Error in loading result csv file\n\n{}".format(logFixFmt(
                "Please ensure the first column in 'objid'. If the file is corrupted, delete \
                it and rerun!"
            )))

        self.galaxies = [(str(objid), (ra, dec))
                        for objid, ra, dec
                        in zip(df["objid"], df["ra"], df["dec"])
                        if str(objid) not in resIDs]

    def __init__ (self, batchName, csvName, bands, rad) :
        """
        Constructor for the batch. Does the following -
            1. Sets up the folders/environment for the batch
            2. Sets the result logger for the batch in the batch folder
            3. Reads in the batch .csv and the result .csv file and decides
            which objects remain to be classified
        """

        self.batchName = batchName
        self.csvName = csvName
        self.__prelude__()
        runlog.info("Successfully created environment for batch")

        # Function to check if the band(s) supplied by the user is valid
        areBandsValid = lambda bs : len([b for b in bs if b in Galaxy.default_bands]) == len(bs) != 0

        ######################################################################
        # If the bands are not valid, a warning is logged
        # This is because the Galaxy object internally takes care of
        # invalid bands
        ######################################################################
        if not areBandsValid(bands) :
            runlog.warning("One or more bands in '{}' invalid\n\n{}".format(bands, logFixFmt(
            "Please ensure that bands are a combination of 'ugri' only!"
            )))
            raise ValueError("Invalid Band. Please use 'ugri'")

        self.bands = bands

        # Sets the result logger for the batch
        self.__setLoggers__()

        # Initialises the galaxy objects that are yet to be classified in this batch
        self.__setBatchList__()

        print("Batch successfully initialised. \
        \nThe classifications will be available at {} \
        \nIn the event of any program crash/error, please check the log file at {} for details"\
        .format(self.resCsvPath, os.path.join(os.getcwd(), runLogPath)))
        print("Number of galaxies to classify - {}".format(len(self.galaxies)))

    def __str__ (self) :
        """ Batch object to string """
        return self.csvPath

    def __len__ (self) :
        """ Length of the batch """
        return len(self.galaxies)

    @property
    def batchFold (self) :
        """ Property attribute - Path of the batch folder """
        return os.path.join (os.getcwd(), Batch.batchRoot, self.batchName)

    @property
    def fitsFold (self) :
        """ Property attribute - Path of the FITS folder for the batch """
        return os.path.join (self.batchFold, "FITS")

    @property
    def resFold (self) :
        """ Property attribute - Path of the folder that contains result images
        post classificaton """
        return os.path.join(self.batchFold, "Results")

    @property
    def csvPath (self) :
        """ Property attribute - Path of the csv File """
        return os.path.join(self.batchFold, self.csvName)

    @property
    def resCsvPath (self) :
        """ Property attribute - Path of the result .csv file """
        return os.path.join(self.batchFold, self.csvName[:-4] + "_result.csv")

    @property
    def pureCsvPath (self) :
        """ Property attribute - Path of the pure .csv file """
        return os.path.join(self.batchFold, self.csvName[:-4] + "_pure_pids.csv")

    @property
    def impureCsvPath (self) :
        """ Property attribute - Path of the impure .csv file """
        return os.path.join(self.batchFold, self.csvName[:-4] + "_impure.csv")

    @property
    def logPath (self) :
        """ Property attribute - Path of the log file for the batch """
        return os.path.join(os.getcwd(), self.batchFold, "{}.log".format(self.batchName))

    def classifyGal (self, args) :
        """
        Performs the following for an argument
            1. Download the FITS file if necessary
            2. Read the FITS file and obtain the cutout
            3. Smoothen the cutout data
            3. Find the hull region where peak searching is done
            4. Filter the bands in this galaxy where signal is unlikely to be found
            5. Fit the intensity distribution to a light profile
            6. Find the peaks using Stochastic Hill Climbing and DFS
        """

        try :
            args += (self.fitsFold, self.bands)
            g = Galaxy(*args)

            g.download()
            runlog.info("{} --> Downloaded".format(g.objid))
            g.cutout()
            runlog.info("{} --> Loaded and done cutout".format(g.objid))
            g.smoothen()
            runlog.info("{} --> Smoothed".format(g.objid))
            g.hullRegion()
            runlog.info("{} --> Found hull region".format(g.objid))
            g.filter()
            runlog.info("{} --> Filtered".format(g.objid))
            g.fitProfile()
            runlog.info("{} --> Fit intensity profile".format(g.objid))
            g.setPeaks()
            runlog.info("{} --> Found peaks".format(g.objid))

            ret = (g.csvLine(), g.progressLine())
            csvLine, progressLine = ret
            purity, rep_band = pp.get_purity_band(g)
            if rep_band is not None :
                if purity :
                    bands = pp.get_bands_csv (g, self.bands)

                    pid1, pid2 = pp.peak_to_objid(g.cutouts[rep_band].wcs, g.peaks[rep_band].filtPeaks)
                    self.purelog.info(f"{g.objid},{bands},{pid1},{pid2}")
                else :
                    self.impurelog.info(csvLine)
                    for b in g.bands :
                        if len(g.peaks[b].filtPeaks) != 2 :
                            continue

                        img = g.getPeaksMarked(b, True)
                        plt.imshow(img)
                        plt.axis('off')
                        plt.savefig(os.path.join(self.resFold, "{}-{}_result.png".format(g.objid, b)),
                                    bbox_inches='tight',
                                    pad_inches=0)
                        plt.close()

                    runlog.info("{} --> Results for manual impure classification".format(g.objid))
        except Exception as e :
            runlog.info("{} --> ERROR : {}".format(g.objid, e))
            ret = (str(g.objid) + 2*len(self.bands)*",ERROR", str(g.objid) + " -->" + len(self.bands)*" ERROR")

        g.delete()
        runlog.info("{} --> Deleted files".format(g.objid))
        del g
        return ret

    def classifySerial (self) :
        self.gals = []
        for i, args in enumerate(self.galaxies) :
            csvLine, progLine = self.classifyGal(args)
            self.reslog.info(csvLine)
            print("{}. {}".format(i+1, progLine))
