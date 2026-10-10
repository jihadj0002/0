web: python manage.py migrate --noinput && gunicorn theMatrixAi.wsgi:application --access-logfile - --access-logformat '%(t)s "%(r)s" %(s)s %(b)s' --timeout 300 --workers 4 --threads 2
worker: python manage.py rqworker-pool default email --num-workers 10
