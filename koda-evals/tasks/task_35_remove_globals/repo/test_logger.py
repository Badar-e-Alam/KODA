import logger

def test_default_info():
    logger.info("hello")  # should print

def test_per_instance_level():
    l = logger.Logger("DEBUG")
    # debug should print for this instance
    l.debug("test")

def test_quiet_instance():
    l = logger.Logger("ERROR")
    # info should not print
    l.info("silent")
