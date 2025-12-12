import os
from ftplib import FTP
from django.core.files.storage import Storage
from django.conf import settings


class FTPStorage(Storage):
    """
    Custom Django File Storage using FTP upload.
    """
    print("Initializing FTPStorage with settings:")
    print(f"FTP_HOST: {os.environ.get('FTP_HOST')}")


    def __init__(self):
        self.host = os.environ.get("FTP_HOST")
        self.user = os.environ.get("FTP_USER")
        self.passwd = os.environ.get("FTP_PASS")
        self.media_dir = os.environ.get("FTP_MEDIA_DIR", "/media/")
        self.base_url = os.environ.get("FTP_BASE_URL")


    def _connect(self):
        ftp = FTP(self.host)
        ftp.login(self.user, self.passwd)
        print("Connected to FTP server")
        return ftp

    def _save(self, name, content):
        import traceback
        print("Saving file:", name)

        try:
            ftp = self._connect()
        except Exception as e:
            print("FTP CONNECT ERROR:", e)
            print(traceback.format_exc())
            raise

        filepath = f"{self.media_dir}{name}"
        print("FTP path:", filepath)

        try:
            # Create directories
            dirs = filepath.split("/")[:-1]
            path = ""
            for d in dirs:
                if d:
                    path += "/" + d
                    try:
                        ftp.mkd(path)
                    except Exception as ex:
                        print("MKDIR err (ignored):", ex)

            ftp.storbinary(f"STOR {filepath}", content)
            print("UPLOAD OK:", filepath)

        except Exception as e:
            print("FTP UPLOAD ERROR:", e)
            print(traceback.format_exc())
            raise

        finally:
            ftp.quit()

        return name


    def exists(self, name):
        # We assume overwrite allowed or check externally
        return False

    def url(self, name):
        return f"{self.base_url}{name}"
