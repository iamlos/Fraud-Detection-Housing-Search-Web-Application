from sklearn.externals import joblib
from flask import Flask
from flask import Response
from flask import request
import simplejson as json
import fraud_algorithm as algorithm
from MongoDB import LISTDBCLIENT
from MongoDB import CRAWLERDBCLIENT
from bson.objectid import ObjectId
import logging
from httplib2 import Http
import urllib
from celery import Celery

uiEmailUrl = "http://54.175.222.115:8888/fraud_report"

app = Flask(__name__)
app.config['version'] = 'Version 1.0'

app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'
celeryClient = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celeryClient.conf.update(app.config)

@app.route('/')
def index():
    return "Fraud detection Flask application"


# GET /api/fraud/listing/{listing_id}
@app.route('/api/fraud/listing/<listing_id>')
def calculate_listing_score(listing_id):
    listing_collection = LISTDBCLIENT.globallisting.listings
    json_listing = None
    try:
        json_listing = listing_collection.find_one({'_id': ObjectId(listing_id)})
    except Exception, e:
        logger.debug(str(e))
        return Response(json.dumps({'error': str(e)}), status=404, mimetype='application/json')
    if json_listing is None:
        logger.debug('Can not find the listing with id %s' % listing_id)
        return Response(json.dumps({'error': 'Can not find the listing'}), status=404, mimetype='application/json')

    # json_listing = file_parser.file_to_json('listing.json')


    # score is 0 to 5, 0 meaning that it is not a scam
    score = 0.0
    estimated_rent = 0.0
    latest_date = ''
    positive_words = []
    negative_words = []
    duplicated_image = []
    duplicated_address = []

    try:

        # ADDRESS
        address = json_listing['address']
        if address is None or address['zipcode'] is None:
            # No address, increase the score
            score += 2
        else:
            # RENT PRICE
            estimated_rent, latest_date = algorithm.get_estimated_rent(address['zipcode'], json_listing['area'])
            score += algorithm.check_price(estimated_rent, json_listing['price'])

        # DUPLICATE IMAGES
        image_score, duplicated_image = algorithm.check_images(json_listing['image'],
                                                               address['lat'],
                                                               address['lon'],
                                                               json_listing['price'])
        score += image_score

        # DUPLICATE ADDRESS
        address_score = 0
        try:
            address_score, duplicated_address = algorithm.check_address(address['state'],
                                                                        address['city'],
                                                                        address['street'],
                                                                        json_listing['price'])
        except Exception, e:
            logger.error('Can not parse the address of the listing with id %s.' % listing_id)

        score += address_score

        # DESCRIPTION
        # TODO need to modify the description score
        description = json_listing['description']
        desc_score = algorithm.check_description(description, minmax, clf)
        score += (1 - desc_score)

        # get positive and negative words
        positive_words, negative_words = algorithm.get_positive_negative_words(description)

        # TODO scale the score to 0-5

        json_resp = json.dumps({'listing_id': listing_id,
                                'score': score,
                                'avg_price': {
                                    'price': round(estimated_rent, 2),
                                    'latest_date': latest_date
                                },
                                'duplicated_listings': [
                                    {
                                        'type': 'image',
                                        'duplicates': duplicated_image
                                    },
                                    {
                                        'type': 'address',
                                        'duplicates': duplicated_address
                                    }
                                ],
                                'positive_words': positive_words,
                                'negative_words': negative_words
                                })

        resp = Response(json_resp, status=200, mimetype='application/json')
        return resp
    except Exception, e:
        return Response(json.dumps({'error': str(e)}), status=500, mimetype='application/json')


@app.route('/api/fraud/search', methods=['GET'])
def calculate_scraped_listing_score():
    crawler_listing_id = request.args.get('crawler_listing_id')
    print 'listing id is: %s' %crawler_listing_id
    email_address = request.args.get('email_address')
    calculateScore.apply_async(args=[crawler_listing_id,email_address])
    return jsonify({"status":"Fraud in progress"})

@celeryClient.task
def calculateScore(crawler_listing_id, email_address):
    crawler_collection = CRAWLERDBCLIENT.scrapy.scrapyItems
    try:
        json_listing = crawler_collection.find_one({'listid': crawler_listing_id})
    except Exception, e:
        logger.debug(str(e))
        return Response(json.dumps({'error': str(e)}), status=404, mimetype='application/json')
    if json_listing is None:
        logger.debug('Can not find the listing with id %s' %crawler_listing_id)
        return Response(json.dumps({'error': 'Can not find the listing'}), status=404, mimetype='application/json')

    # score is 0 to 5, 0 meaning that it is not a scam
    score = 0.0
    estimated_rent = 0.0
    latest_date = ''
    positive_words = []
    negative_words = []
    duplicated_image = []
    duplicated_address = []

    try:
        listing_url = json_listing['url']
        # ADDRESS
        address = json_listing['address']
        if address is None or address['zipcode'] is None:
            # No address, increase the score
            score += 2
        else:
            # RENT PRICE
            try:
                estimated_rent, latest_date = algorithm.get_estimated_rent(address['zipcode'], json_listing['area'])
                score += algorithm.check_price(estimated_rent, json_listing['price'])
            except Exception, e:
                logger.error('Can not calculate the estimated price for the '
                             'listing with id %s. %s' %(crawler_listing_id, str(e)))

        try:
            # DUPLICATE IMAGES
            image_score, duplicated_image = algorithm.check_images(json_listing['image'],
                                                               address['lat'],
                                                               address['lon'],
                                                               json_listing['price'],
                                                               crawler_listing_id)
            score += image_score
        except Exception, e:
            logger.error('Can not search images for the '
                         'listing with id %s. %s' %(crawler_listing_id, str(e)))

        # DUPLICATE ADDRESS
        address_score = 0
        try:
            address_score, duplicated_address = algorithm.check_address(address['state'],
                                                                        address['city'],
                                                                        address['street'],
                                                                        json_listing['price'],
                                                                        crawler_listing_id)
            score += address_score
        except Exception, e:
            logger.error('Can not parse the address of the listing with id %s.' % crawler_listing_id)

        try:
            # DESCRIPTION
            # TODO need to modify the description score
            description = json_listing['description']
            desc_score = algorithm.check_description(description, minmax, clf)
            score += (1 - desc_score)

            # get positive and negative words
            positive_words, negative_words = algorithm.get_positive_negative_words(description)
        except Exception, e:
            logger.error('Can not parse the description of the listing with id %s.' % crawler_listing_id)

        # TODO scale the score to 0-5

        json_resp = json.dumps({'listing_id': crawler_listing_id,
                                'email_address': email_address,
                                'listing_url': listing_url,
                                'score': score,
                                'avg_price': {
                                    'price': round(estimated_rent, 2),
                                    'latest_date': latest_date
                                },
                                'duplicated_listings': [
                                    {
                                        'type': 'image',
                                        'duplicates': duplicated_image
                                    },
                                    {
                                        'type': 'address',
                                        'duplicates': duplicated_address
                                    }
                                ],
                                'positive_words': positive_words,
                                'negative_words': negative_words
                                })

        data=json.loads(json_resp)
        http_obj = Http()
        resp, content = http_obj.request(
        uri=uiEmailUrl,
        method='POST',
        headers={'Content-Type': 'application/json; charset=UTF-8'},
        body=json.dumps(data),
        )
        return resp
    except Exception, e:
        logger.error('Can not generate fraud detection report with id %s.' % crawler_listing_id)
        return "Failed to generate fraud detection score:" + str(e)


# POST /api/fraud/description
@app.route('/api/fraud/description', methods=['POST'])
def calculate_description_score():
    try:
        json_request = request.json
        description = json_request['description']
        desc_score = algorithm.check_description(description, minmax, clf)

        json_resp = json.dumps({'description': description, 'score': desc_score})
        resp = Response(json_resp, status=200, mimetype='application/json')
        return resp
    except Exception, e:
        return Response(json.dumps({'error': str(e)}), status=500, mimetype='application/json')


# POST /api/fraud/price
@app.route('/api/fraud/rent', methods=['POST'])
def calculate_estimated_price():
    try:
        json_request = request.json
        zip_code = json_request['zipcode']
        area = json_request['area']
        estimated_rent, latest_date = algorithm.get_estimated_rent(zip_code, area)

        json_resp = json.dumps({'area': area, 'zipcode': zip_code,
                                'estimated_rent': estimated_rent,
                                'latest_date': latest_date})
        resp = Response(json_resp, status=200, mimetype='application/json')
        return resp
    except Exception, e:
        logger.debug(str(e))
        return Response(json.dumps({'error': str(e)}), status=500, mimetype='application/json')


@app.before_first_request
def run_on_start():
    print 'Received the first scoring request!'


if __name__ == "__main__":
    print 'Started running!'
    clf = joblib.load('model_isof.pkl')
    minmax = joblib.load('minmax.pkl')
    standard = joblib.load('standard.pkl')
    # set up a logger
    logging.basicConfig(filename='../logs/fraud.log',
                        format='%(asctime)s %(message)s',
                        level=logging.DEBUG)
    # get the root logger
    logger = logging.getLogger()
    app.run(host="0.0.0.0", port=8080, debug=True)
