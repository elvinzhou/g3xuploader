"""Flight data uploaders."""

from avcardtool.flight_data.uploaders.cloudahoy import CloudAhoyUploader
from avcardtool.flight_data.uploaders.flysto import FlyStoUploader
from avcardtool.flight_data.uploaders.savvy_aviation import SavvyAviationUploader
from avcardtool.flight_data.uploaders.carryd import CarrydUploader

__all__ = [
    'CloudAhoyUploader',
    'FlyStoUploader',
    'SavvyAviationUploader',
    'CarrydUploader',
]

# Registry of all available uploaders
UPLOADERS = {
    'cloudahoy': CloudAhoyUploader,
    'flysto': FlyStoUploader,
    'savvy_aviation': SavvyAviationUploader,
    'carryd': CarrydUploader,
}
