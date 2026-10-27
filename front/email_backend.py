import logging

from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend

logger = logging.getLogger(__name__)


class FallbackEmailBackend(SmtpEmailBackend):
    """SMTP backend that falls back to port 465 (SSL) if port 587 (TLS) fails."""

    def open(self):
        configs = self._connection_configs()
        for idx, cfg in enumerate(configs):
            self.host = cfg["host"]
            self.port = cfg["port"]
            self.use_tls = cfg["use_tls"]
            self.use_ssl = cfg["use_ssl"]
            self.timeout = cfg["timeout"]
            try:
                result = super().open()
                if result:
                    logger.info("SMTP connected to %s:%s", self.host, self.port)
                return result
            except OSError as e:
                logger.warning(
                    "SMTP connection to %s:%s failed: %s%s",
                    cfg["host"], cfg["port"], e,
                    " — trying fallback..." if idx < len(configs) - 1 else "",
                )
                self.connection = None
        return False

    def _connection_configs(self):
        primary = {
            "host": self.host,
            "port": self.port,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "timeout": self.timeout,
        }
        if self.port == 587 and self.use_tls and not self.use_ssl:
            fallback = {
                "host": self.host,
                "port": 465,
                "use_tls": False,
                "use_ssl": True,
                "timeout": self.timeout,
            }
            return [primary, fallback]
        return [primary]