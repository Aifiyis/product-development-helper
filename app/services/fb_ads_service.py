import os


def collect_fb_ads(domain, threshold=0):
    if not os.environ.get("FB_ADS_LIBRARY_TOKEN") and not os.environ.get("META_BUSINESS_TOKEN"):
        return []
    return []
