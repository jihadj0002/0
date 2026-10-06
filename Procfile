web: python manage.py migrate --noinput && gunicorn theMatrixAi.wsgi:application --access-logfile - --access-logformat '%(t)s "%(r)s" %(s)s %(b)s'
