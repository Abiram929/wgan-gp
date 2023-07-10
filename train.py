#!/usr/bin/env python
# coding: utf-8

import os
import comet_ml
import torch
import torch.nn as nn
import torchvision.transforms as tf
from PIL import Image
import torch.autograd as autograd
from networks import Discriminator,Generator2
from loss_network import LossNetwork
from loss import content_style_loss,adv_loss_d,adv_loss_g,gaze_loss_d,gaze_loss_g,reconstruction_loss
from PIL import Image
import numpy as np
#import lightning as L
import yaml
from munch import DefaultMunch
from tqdm import tqdm
from comet_ml.integration.pytorch import log_model

# The images files have the form "ID_2m_0P_xV_yH_z.jpg" where ID is the ID of the person, 2m is fixed, 0P means head pose of 0 degrees (only head pose used in this notebook)
# x is the vertical orientation, y is the horizontal orientation and z is either L for left or R for right eye (note that the right eye patch was flipped horizontally).
# In training the images are grouped as follows:
# For a given person and a given eye (R or L) all orientations are grouped together. One element of the data set is of the form
# imgs_r,angles_r,labels,imgs_t,angles_g where imgs_r is considered the "real" image with orientation angles_r, or x_r in the paper,
# imgs_t with orientation angles_g is the image of the same person with different orientation (could be the same image since we go through a double loop) and the label is the ID of the person



with open('config.yaml') as f:
    config = yaml.safe_load(f)
config = DefaultMunch.fromDict(config)

if config.use_comet is not None:
    if config.use_comet=='offline':
        print("using comet offline")
        experiment = comet_ml.OfflineExperiment(project_name=config.comet_project, workspace=config.comet_workspace,
                                                auto_metric_logging=False, auto_output_logging=False)
    else:
        experiment = comet_ml.Experiment(project_name=config.comet_project, workspace=config.comet_workspace,
                                         auto_metric_logging=False, auto_output_logging=False)
    experiment.log_parameters(config)
    #experiment.set_name(config.comet_experiment_name)
else:
    print("don't use comet")


class PreProcessData():
    def __init__(self, dir_path, transform=None):
        super().__init__()
        self.transform = transform
        self.ids=50
        self.data_path = dir_path
        self.file_names = [f for f in os.listdir(self.data_path)
                      if f.endswith('.jpg')]
        self.file_dict = dict()
        for f_name in self.file_names:
            fields = f_name.split('.')[0].split('_')
            identity = fields[0]
            head_pose = fields[2]
            side = fields[-1]
            key = '_'.join([identity, head_pose, side])
            if key not in self.file_dict.keys():
                self.file_dict[key] = []
                self.file_dict[key].append(f_name)
            else:
                self.file_dict[key].append(f_name)
        self.train_images = []
        self.train_angles_r = []
        self.train_labels = []
        self.train_images_t = []
        self.train_angles_g = []

        self.test_images = []
        self.test_angles_r = []
        self.test_labels = []
        self.test_images_t = []
        self.test_angles_g = []
        self.preprocess()
    def preprocess(self):

        for key in self.file_dict.keys():

            if len(self.file_dict[key]) == 1:
                continue

            idx = int(key.split('_')[0])
            flip = 1
            if key.split('_')[-1] == 'R':
                flip = -1

            for f_r in self.file_dict[key]:

                file_path = os.path.join(self.data_path, f_r)

                h_angle_r = flip * float(
                    f_r.split('_')[-2].split('H')[0]) / 15.0
                    
                v_angle_r = float(
                    f_r.split('_')[-3].split('V')[0]) / 10.0
                    

                for f_g in self.file_dict[key]:

                    file_path_t = os.path.join(self.data_path, f_g)

                    h_angle_g = flip * float(
                        f_g.split('_')[-2].split('H')[0]) / 15.0
                        
                    v_angle_g = float(
                        f_g.split('_')[-3].split('V')[0]) / 10.0
                        

                    if idx <= self.ids:
                        self.train_images.append(file_path)
                        self.train_angles_r.append([h_angle_r, v_angle_r])
                        self.train_labels.append(idx - 1)
                        self.train_images_t.append(file_path_t)
                        self.train_angles_g.append([h_angle_g, v_angle_g])
            if idx > self.ids :
                self.test_images.append(file_path)
                self.test_angles_r.append([h_angle_r, v_angle_r])
                self.test_labels.append(idx - 1)
                self.test_images_t.append(file_path_t)
                self.test_angles_g.append([h_angle_g, v_angle_g])
    def training_data(self):
            return self.train_images,self.train_angles_r,self.train_labels,self.train_images_t,self.train_angles_g
    def testing_data(self):
            return self.test_images,self.test_angles_r,self.test_labels,self.test_images_t,self.test_angles_g
class MyDataset(torch.utils.data.Dataset):
    def __init__(self, images,angles_r,labels,images_t,angles_g, transform=None):
        super().__init__()
        self.transform = transform
        self.images=images
        self.angles_r=angles_r
        self.labels=labels
        self.images_t=images_t
        self.angles_g=angles_g
    def __getitem__(self, index):
        return (
            self.transform(Image.open(self.images[index])),
                torch.tensor(self.angles_r[index]),
                self.labels[index],
            self.transform(Image.open(self.images_t[index])),
                torch.tensor(self.angles_g[index]))
        
    def __len__(self):
        return len(self.images)
data=PreProcessData(config.data_path)
transform=tf.Compose([tf.ToTensor(),tf.Resize((64,64),antialias=True)])
train_dataset=MyDataset(*data.training_data(),transform=transform)
test_dataset=MyDataset(*data.testing_data(),transform=transform)

#dataset=MyDataset(dir_path=config.data_path,transform=transform)
train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=config.batch_size, shuffle=True)
test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=len(test_dataset), shuffle=False)
device='cuda' if torch.cuda.is_available() else 'cpu'


if os.path.isfile('discriminator.pth'):
    discriminator=torch.load('discriminator.pth')
    print('loaded discriminator')
else:
    discriminator=Discriminator()
    print('created discriminator')
if os.path.isfile('generator.pth'):
    generator=torch.load('generator.pth')
    print('loaded generator')
else:
    generator=Generator2()
    print('created generator')

generator=generator.to(device)
discriminator=discriminator.to(device)
LR=config.lr
beta1=config.beta1
beta2=config.beta2
optimizer_g = torch.optim.Adam(generator.parameters(), LR,betas=(beta1, beta2))
optimizer_d = torch.optim.Adam(discriminator.parameters(), LR,betas=(beta1, beta2))

loss_network=LossNetwork()
loss_network=loss_network.to(device)

def generator_step(generator,discriminator,loss_network,batch):
    imgs_r, angles_r, _, imgs_t, angles_g=batch
    optimizer_g.zero_grad()
    generator.train()
    discriminator.eval()
    x_g=generator(imgs_r,angles_g)
    x_recon=generator(x_g,angles_r)
    loss_adv=adv_loss_g(discriminator,imgs_r,x_g)
    loss2=content_style_loss(loss_network,x_g,imgs_t)
    loss_p=loss2[0]+loss2[1]
    loss_gg=gaze_loss_g(discriminator,x_g,angles_g)
    loss_recon=reconstruction_loss(generator,imgs_r,x_recon)
    loss=loss_adv+config.lambda_p*loss_p+config.lambda_gaze*loss_gg+config.lambda_recon*loss_recon
    loss.backward()
    optimizer_g.step()
    return loss.item()



def discriminator_step(generator,discriminator,batch):
    imgs_r, angles_r, _, _, angles_g=batch
    optimizer_d.zero_grad()
    generator.eval()
    discriminator.train()
    x_g=generator(imgs_r,angles_g)
    loss1=adv_loss_d(discriminator,imgs_r,x_g)
    loss2=gaze_loss_d(discriminator,imgs_r,angles_r)
    loss=loss1+config.lambda_gaze*loss2
    loss.backward()
    optimizer_d.step()
    return loss.item()



def recover_image(img):
    img=img.cpu().numpy().transpose(0, 2, 3, 1)*255
    return img.astype(np.uint8)
def save_images(imgs):
    height=recover_image(imgs[0])[0].shape[0]
    width=recover_image(imgs[0])[0].shape[1]
    total_width=width*len(imgs)
    
    new_im = Image.new('RGB', (total_width+len(imgs), height))
    for i,img in enumerate(imgs):
        result = Image.fromarray(recover_image(img)[0])
        new_im.paste(result, (i*width+i,0))
    return new_im
# z=0
# for epoch in tqdm(range(config.epochs)):
#     for batch in tqdm(train_loader):
#         z+=1
loop=tqdm(range(config.epochs))

for epoch in loop:
    loop.set_description(f"Epoch [{epoch+1}/{config.epochs}]")
    batch_count=0
    loss_d,loss_g=0.,0.
    for batch in tqdm(train_loader):
        batch_count+=1
        batch=[x.to(device) for x in batch]
        l_d=discriminator_step(generator,discriminator,batch)
        loss_d=0.9*loss_d+0.1*l_d
        if batch_count%config.critic_iter_per_gen==0:
            l_g=generator_step(generator,discriminator,loss_network,batch)
            loss_g=0.9*loss_g+0.1*l_g
        if batch_count%config.image_save_freq==0:
            imgs=[batch[0]]
            for h in [-15,-10,-5,0,5,10,15]:
                    a=torch.tile(torch.tensor([h/15.,0.]),[32,1])
                    a=a.to(device)
                    y=generator(batch[0],a)
                    imgs.append(y.detach())
            filename="./debug/{}_{}.png".format(epoch,batch_count)
            im=save_images(imgs)
            im.save(filename)
            if config.use_comet is not None:
                experiment.log_image(im)
    loop.set_postfix(loss_d=loss_d,loss_g=loss_g)
    print(l_d,l_g)
    if config.use_comet is not None:
        metrics={'loss_d':l_d,'loss_g':l_g}
        experiment.log_metrics(metrics, epoch=epoch)
    if epoch%config.model_save_freq==0:
        # if config.use_comet is not None:
        #     log_model(experiment,generator,"generator")
        #     log_model(experiment,discriminator,"discriminator")
        # torch.save(generator, './generator.pth')
        # torch.save(discriminator, './discriminator.pth')
        orig=next(iter(test_loader))[0].to(device)
        imgs=[orig]
        for h in [-15,-10,-5,0,5,10,15]:
                    a=torch.tile(torch.tensor([h/15.,0.]),[32,1])
                    a=a.to(device)
                    y=generator(orig,a)
                    imgs.append(y.detach())
     