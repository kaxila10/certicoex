"""Hosted entrypoint. Render supplies PORT; Docker defaults to 10000."""
import os

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.main:app', host='0.0.0.0', port=int(os.environ.get('PORT', '10000')), workers=1)
