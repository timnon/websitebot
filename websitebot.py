import time
import json
import traceback
import time 
import os
import textwrap
import openai
from tqdm import tqdm
from termcolor import colored
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options

# attributes that describe tag
ATTS = ['id','value']
openai.api_key = os.environ['OPENAI_API_KEY']


class WebsiteBot:
    def __init__(self, driver=None, goal=None, url=None):
        self.driver = driver
        self.goal = goal
        self.url = url
        if driver is not None:
            self.driver.get(url)
        self.plans = [f'go to website {url}']
        # sleept to load webpage
        time.sleep(1)


    def _elem2tag(self, element):
        tag = self.driver.execute_script("""
            var attrs = {};
            for (var attr of arguments[0].attributes) {
                attrs[attr.name] = attr.value;
            }
            attrs["x"] = arguments[0].getBoundingClientRect().x;
            attrs["y"] = arguments[0].getBoundingClientRect().y;
            attrs["width"] = arguments[0].offsetWidth;
            attrs["height"] = arguments[0].offsetHeight;
            attrs["text"] = arguments[0].innerText;
            attrs["name"] = arguments[0].tagName.toLowerCase();
            return attrs;
        """, element)
        return tag
    
    
    def extract_tags(self):
        '''
        Extract tags from current website
        '''
        print('>>> extract tags')        
        tag_types = ['button','input','p','h1','h2','h3'] #,'a']
        xpath_query = "//*[" + " or ".join(f"self::{tag_type}" for tag_type in tag_types) + "]"
        elements = self.driver.find_elements(By.XPATH, xpath_query)
        tags = [ self._elem2tag(element) for element in tqdm(elements) ]
        print('#tags extracted:',len(tags))
        return tags


    def cleanse_tags(self, tags: list, y_max=3000) -> list:
        '''
        tags is a list of dictionaries. This method removes all dicts in this list where the
        text-key is contained in the text-key of another dict. Only the dict with the longest
        text-key is kept in case there is a chain of containments.
        '''
        print('>>> cleanse tags')
        cleansed_tags = []
        
        # Sort the tags list by the length of the text-key in descending order
        #tags.sort(key=lambda x: len(x['text']) if 'text' in x else 0)#, reverse=True)
        
        for tag in tags:
            # Do not keep tags which are too far below
            if tag['y'] > y_max:
                continue
            # Do not keep tags that cover no screen space
            if tag['width']==0 or tag["height"]==0:
                continue
            # Do not keep elements which have absolutely no description
            if all( not att in tag for att in ATTS+['text'] ):
                continue
            # Do not keep content tags which do not contain text
            if tag['name'] not in ['button','a','input'] and 'text' not in tag:
                continue
            # Do not keep div-tags where the text is already contained in a tag with longer text (sorted)
            # for cleansed_tag in cleansed_tags:
            #     tag['text'] = tag['text'].replace('\n'+cleansed_tag['text'],'')
            tag['text'] = tag['text'].strip()
            if tag['name'] not in ['button','a','input'] and not tag['text']:
                continue
            # if tag['name'] not in ['button','a','input'] \
            #     and any(tag['text'] in cleansed_tag['text'] for cleansed_tag in cleansed_tags):
            #     continue
            cleansed_tags.append(tag)

        # Sort again according to the position on the screen
        #cleansed_tags = sorted(cleansed_tags,key=lambda x: x['y'])
        print('#tags after cleansing:',len(cleansed_tags))
        return cleansed_tags


    def get_prompt(self,tags,filename='prompt.txt'):
        '''
        Compute prompt
        '''
        print('>>> get prompt')

        prompt = textwrap.dedent(f'''
        OVERALL GOAL: 
        {self.goal}
        ''')

        newline = '\n'
        prompt += textwrap.dedent(f'''
        PREVIOUS STEPS:
        {newline.join(f"{i+1}) {plan}" for i,plan in enumerate(self.plans))}
        ''')

        prompt += textwrap.dedent(f'''        
        CURRENT SITUATION: 
        After the previous steps we are now on a website that contains the following text and tags:
        ''')

        for tag in tags:
            # restrict to descriptive attributes
            tag_ = { k:tag[k] for k in ['name','text']+ATTS if k in tag and tag[k] not in (None,"") }
            if tag['name'] not in ['a','input','button']:
                prompt += tag['text']+'\n'
            else:
                prompt += json.dumps(tag_,ensure_ascii=False)+'\n'

        prompt += textwrap.dedent('''
        NEXT STEPS: 
        Which next steps should we take on this website to achieve the overall goal.
        If cookies need to get accepted, do this first before any other steps.
        Return next steps in the following json-format, do not return anything else:

        {
            plan: '<high-level description of the website, the next steps on this website and a summary of the expected result. Argue first whether cookies need to get accepted or not>',
            steps: [<step1>,<step2>,...],
        }
                
        The steps-attribute should contain a list of steps to execute on this website. 
        A <step> can have the following dict-structures:       
        {"action":"click","tag":<tag>} for a <tag> of type button, a or input. Clicking on an input-tag can sometimes open a drop-down menu which can then be used in the next round. 
        ("action":"fill","value":<value_to_fill>,"tag":<tag>} for a <tag> of type input
        ''')

        open(filename,'w').write('')
        with open(filename,'a',encoding='utf-8') as file:
            file.write(prompt)
        
        return prompt
    

    def run_prompt(self,prompt):
        print('>>> run prompt')
        print(colored(prompt, 'green'))

        response = openai.ChatCompletion.create(
        model = 'gpt-4',
        #model = 'gpt-3.5-turbo',
        #prompt=prompt,
        messages = [{"role": "user", "content": prompt}],
        temperature=1,
        max_tokens=500,
        top_p=1.0,
        frequency_penalty=0.0,
        presence_penalty=0.0
        )
        plan = response['choices'][0]['message']['content']
        start = plan.index('{')
        end = plan.rindex('}') + 1  # Adding 1 because slicing is end-exclusive
        plan = json.loads(plan[start:end])
        print(colored(json.dumps(plan,indent=1), 'light_blue'))
        return plan
    

    def _get_element(self,driver,tag):
        '''
        tag is a dictionary which describes an html tag:
        tag: the type of the tag
        text: the text contained in the tag
        all other keys correspond to tag attributes.
        This method returns the element in driver that corresponds to this tag
        '''
        
        # Construct xpath query to find the element
        xpath_query = f"//{tag['name']}"

        added_filter = False
        if 'id' in tag:
            xpath_query += f"[@id='{tag['id']}']"
            added_filter = True
        elif 'text' in tag:
            xpath_query += f"[normalize-space(.)='{tag['text']}']"
            added_filter = True
        if not added_filter:
            print('element description not found')
            return
        #print('xpath-query:',xpath_query)
        element = driver.find_element(By.XPATH, xpath_query)
        
        # Add attributes to the xpath query
        # for att, value in tag.items():
        #     if att not in ['type','text']:
        #         xpath_query += f"[@{att}='{value}']"
        #breakpoint()
        #time.sleep(1)
        return element


    def execute_steps(self,steps):
        print('#steps to execute:',len(steps))
        for step in steps:
            time.sleep(1)
            tag = step['tag']
            n_its = 1
            for _ in range(n_its):
                try:
                    if step['action'] == 'click':
                        print('click:',tag)
                        element = self._get_element(driver,tag)
                        element.click()
                    elif step['action'] == 'fill':
                        print(f"fill with value {step['value']}:",tag)
                        element = self._get_element(driver,tag)
                        element.clear()
                        element.send_keys(step['value'])
                    # Find the body or html element (covers the entire page)
                    body_element = driver.find_element(By.TAG_NAME, "body")
                    actions = ActionChains(driver)
                    actions.move_to_element_with_offset(body_element, 0, 0).click()
                    break
                except:
                    print('sth went wrong with element')
                    traceback.print_exc()
            time.sleep(1)


    def run(self):
        while True:   
            #breakpoint()
            time.sleep(1)
            tags = self.extract_tags()
            tags = self.cleanse_tags(tags)
            prompt = self.get_prompt(tags)
            plan = self.run_prompt(prompt)
            self.plans.append(plan['plan'])
            steps = plan['steps']
            self.execute_steps(steps)
            time.sleep(1)
            print(colored('=========> next round','light_red'))



# define url and goal
url = 'https://shop.sbb.ch/de/buying/pages/fahrplan/fahrplan.xhtml'
goal = '''
book a train ticket from Zurich to Bern on October 1st around 4pm. 
Use the name "Max Mustermann" with email "max.mustermann@web.ch and birthdate "1.1.2000". 
'''

# url = 'https://www.ikea.com/ch/de/'
# goal = 'order a billy shelf to address Tim Nonner, Huebwisstrasse 12, 8117 Faellanden   '

# url = 'https://www.orellfuessli.ch/'
# goal = 'order the Bible to address Tim Nonner, Huebwisstrasse 12, 8117 Faellanden   '

#url = 'https://www.just-eat.ch/'
# url = 'https://www.dieci.ch/en/'
# goal = 'Order a pizza salami to Huebwisstrasse 12 in Fällanden'


print(colored('GOAL:','light_red'), goal)
print(colored('URL:','light_red'), url)
print('                ')


# create selenium driver
chrome_options = Options()
chrome_options.add_argument("--headless --window-size=2000,1000 --blink-settings=imagesEnabled=false")
#chrome_options.add_argument("--headless")
driver = webdriver.Chrome(chrome_options)

# change window size
screen_width = driver.execute_script("return window.screen.availWidth")
screen_height = driver.execute_script("return window.screen.availHeight")
driver.set_window_size(width=int(screen_width*0.65),height=screen_height*0.95)

# create and run bot
bot = WebsiteBot(driver,goal,url)
bot.run()
driver.close()








        














