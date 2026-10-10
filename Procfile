web: python manage.py migrate --noinput && (python manage.py rqworker-pool default email --num-workers 5 &) && gunicorn theMatrixAi.wsgi:application --bind 0.0.0.0:${PORT:-8000} --access-logfile - --access-logformat '%(t)s "%(r)s" %(s)s %(b)s' --timeout 300 --workers 4 --threads 2
worker: python manage.py rqworker-pool default email --num-workers 20
