import logging
import socket
import ssl

from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test SMTP connectivity to the configured email host and optionally send a test email."

    def add_arguments(self, parser):
        parser.add_argument("--to", help="Send a test email to this address")
        parser.add_argument("--host", help="SMTP host to test (default: from settings)")

    def handle(self, *args, **options):
        host = options["host"] or getattr(settings, "EMAIL_HOST", None)
        port = getattr(settings, "EMAIL_PORT", 587)
        use_tls = getattr(settings, "EMAIL_USE_TLS", True)
        use_ssl = getattr(settings, "EMAIL_USE_SSL", False)
        user = getattr(settings, "EMAIL_HOST_USER", "")
        timeout = getattr(settings, "EMAIL_TIMEOUT", 15)

        if not host:
            self.stderr.write("ERROR: EMAIL_HOST is not configured.")
            return

        self.stdout.write(f"Testing SMTP connectivity to {host}...")
        self.stdout.write(f"  Port: {port}, TLS: {use_tls}, SSL: {use_ssl}")
        self.stdout.write(f"  User: {user}")
        self.stdout.write(f"  Timeout: {timeout}s")

        self._test_tcp(host, port, timeout)

        fallback_ports = []
        if port == 587 and use_tls:
            fallback_ports.append(465)
        elif port == 465 and use_ssl:
            fallback_ports.append(587)

        for fport in fallback_ports:
            self.stdout.write(f"\nTesting fallback port {fport}...")
            self._test_tcp(host, fport, timeout)

        if options["to"]:
            self._send_test(options["to"])

    def _test_tcp(self, host, port, timeout):
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            self.stdout.write(self.style.SUCCESS(f"  port {port}: TCP connection OK"))
            if port == 587:
                try:
                    context = ssl.create_default_context()
                    with context.wrap_socket(sock, server_hostname=host) as ssock:
                        self.stdout.write(f"  port {port}: TLS handshake OK (cipher={ssock.cipher()[0]})")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  port {port}: TLS handshake failed: {e}"))
            elif port == 465:
                try:
                    context = ssl.create_default_context()
                    with context.wrap_socket(sock, server_hostname=host) as ssock:
                        self.stdout.write(f"  port {port}: SSL handshake OK (cipher={ssock.cipher()[0]})")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  port {port}: SSL handshake failed: {e}"))
            else:
                sock.close()
        except OSError as e:
            self.stdout.write(self.style.ERROR(f"  port {port}: {e}"))

    def _send_test(self, to_email):
        from django.core.mail import send_mail

        self.stdout.write(f"\nSending test email to {to_email}...")
        try:
            send_mail(
                subject="TheMatrixAi — SMTP test",
                message="This is a test email from TheMatrixAi. If you received this, SMTP is working correctly.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
                fail_silently=False,
            )
            self.stdout.write(self.style.SUCCESS(f"Test email sent to {to_email}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to send test email: {e}"))