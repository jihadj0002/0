Generic template
Updated: Apr 22, 2026
The generic template is a simple structured message that includes a title, subtitle, image, and up to three buttons. You may also specify a default_action object that sets a URL that is opened in the Messenger webview when the template is tapped.
Carousel
The Messenger Platform supports sending a horizontally scrollable carousel of generic templates. To create a scrollable carousel, include up to 10 generic templates in the elements array of the payload.
Request URI
https://graph.facebook.com/v25.0/me/messages?access_token={PAGE_ACCESS_TOKEN}
Payload properties
Property	Type	Description
template_type
String
Must be generic.
elements
Array
An array of element objects that describe each item. Maximum of 10 elements.
sharable
Boolean
Optional. Set to true to enable the native share button in Messenger for the template message. Defaults to false.
element properties
At least one property must be set in addition to title.
Property	Type	Description
title
String
The title to display in the template. 80 character limit.
subtitle
String
Optional. The subtitle to display in the template. 80 character limit.
image_url
String
Optional. The URL of the image to display in the template.
default_action
Object
Optional. The default action executed when the template is tapped. Accepts the same properties as URL button, except title.
buttons
Array
Optional. An array of buttons to append to the template. Maximum of 3 buttons per element.
Sample request
curl -X POST -H "Content-Type: application/json" -d '{
  "recipient":{
    "id":"<PSID>"
  },
  "message":{
    "attachment":{
      "type":"template",
      "payload":{
        "template_type":"generic",
        "elements":[
           {
            "title":"Welcome!",
            "image_url":"https://raw.githubusercontent.com/fbsamples/original-coast-clothing/main/public/styles/male-work.jpg",
            "subtitle":"We have the right hat for everyone.",
            "default_action": {
              "type": "web_url",
              "url": "https://www.originalcoastclothing.com/",
              "webview_height_ratio": "tall"
            },
            "buttons":[
              {
                "type":"web_url",
                "url":"https://www.originalcoastclothing.com/",
                "title":"View Website"
              },{
                "type":"postback",
                "title":"Start Chatting",
                "payload":"DEVELOPER_DEFINED_PAYLOAD"
              }
            ]
          }
        ]
      }
    }
  }
}' "https://graph.facebook.com/v25.0/me/messages?access_token=<PAGE_ACCESS_TOKEN>"
Sample response
{
  "recipient_id": "1254477777772919",
  "message_id": "AG5Hz2Uq7tuwNEhXfYYKj8mJEM_QPpz5jdCK48PnKAjSdjfipqxqMvK8ma6AC8fplwlqLP_5cgXIbu7I3rBN0P"
}
Best practices
Use for messages with a consistent information hierarchy (for example, product or article previews, weather forecasts).
Use the correct aspect ratio for your image. Photos in the generic template that are not 1.91:1 are scaled or cropped.
Do not use if your message does not have structured information or require hierarchy.
Do not use if you need people to be able to zoom your image to full screen.
Do not use GIFs in the template if you intend for them to be animated. GIFs are supported but are not animated.
Carousel best practices
Use a carousel when there is a priority order to your content — the first item is probably the most interesting.
Strive for consistency. If one bubble has a photo, include a photo in all of them.
Minimize the number of generic templates in your carousel. Too many makes it hard for people to remember all the options.
Do not mix types of content. If you include an article next to a list of products, your experience could cause confusion.
Do not use a carousel when it is important that people see everything in the list. They may not scroll to the end.
