"""Flight data uploaders."""

from avcardtool.flight_data.uploaders.cloudahoy import CloudAhoyUploader
from avcardtool.flight_data.uploaders.flysto import FlyStoUploader
from avcardtool.flight_data.uploaders.savvy_aviation import SavvyAviationUploader
from avcardtool.flight_data.uploaders.maintenance_tracker import MaintenanceTrackerUploader

__all__ = [
    'CloudAhoyUploader',
    'FlyStoUploader',
    'SavvyAviationUploader',
    'MaintenanceTrackerUploader',
]

# Registry of all available uploaders
UPLOADERS = {
    'cloudahoy': CloudAhoyUploader,
    'flysto': FlyStoUploader,
    'savvy_aviation': SavvyAviationUploader,
    'maintenance_tracker': MaintenanceTrackerUploader,
}
